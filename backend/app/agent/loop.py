"""ReAct 主循环（D31）：整个系统只有一个主节点，其他能力全是工具。

════════════════════════════════════════════════════════════════════
这个文件在做什么
════════════════════════════════════════════════════════════════════
一次用户消息 = 一次 `run_react_turn()`。它把「模型思考 → 调用工具 → 看结果 → 再思考」
循环跑到底，直到模型给出最终回答，或者撞到 Agent 配置的 `max_rounds` 上限。

    用户消息
       │
       ▼
    ┌──────────────────── 每轮循环（最多 max_rounds 次）────────────────────┐
    │ 1. 调 LLM（流式）：一边把 token 推给前端，一边累积模型要求的工具调用    │
    │ 2. 模型要调工具？→ 并发执行（同一轮里的多个调用一起跑）→ 结果回灌消息   │
    │ 3. 模型直接给文本？→ 这就是最终回答，跳出循环                          │
    └───────────────────────────────────────────────────────────────────────┘
       │
       ▼
    最终回答 + 全部步骤（思考 / 行动 / 观察）

上下文是**每次调用重新拼的**：LLM 无状态，模型不记得上一轮发生过什么，
所以每一轮都要把 system prompt + 完整对话历史一起发过去（这也是为什么长对话会越来越贵）。

════════════════════════════════════════════════════════════════════
三条容易踩坑的设计约定
════════════════════════════════════════════════════════════════════
1. **两条协议都认**（D42）
   模型可以用原生 function calling 返回 `tool_calls`，也可以在文本里写
   `Action: {"tool": "...", "input": {...}}`（文本协议，给不支持 function calling 的模型）。
   两条路最后都汇到同一个执行器 `execute_tool()`，所以提示词用哪一版都不会"一步卡住"。

2. **工具白名单**（D38）
   Agent 页面勾选的工具就是白名单：只执行这一批里"已实现"的工具，模型既看不到、
   也调不动白名单之外的东西。system prompt 里**不再重复列工具清单**——工具由 `tools=`
   参数表达，避免"描述"和"schema"两份真相。

3. **观测是旁路**（D43）
   每个关键步骤都会往 Langfuse 上报，但任何一步失败都必须静默降级，绝不能拖垮主流程。
   为了让 trace 能读，generation 只上报「本轮新增的消息」，完整上下文的规模放在 metadata 里。

════════════════════════════════════════════════════════════════════
本文件的结构（自上而下）
════════════════════════════════════════════════════════════════════
  _extract_json_object / parse_text_action   解析文本协议里的 Action
  Step                                       一条思考/行动/观察（随消息落库 + SSE 外发）
  session_next_window                        会话级窗口号（D50：不再每回合从 W1 重来）
  load_notes / save_notes                    笔记的读与就地覆盖（D53 / D63）
  refine_notes / prune_dead_refs             投影结果的统一修整（换页与收尾共用）
  build_agent_llm                            按 Agent 配置构造 LLM 客户端
  collect_prompt_variables                   提示词模板可用的变量（tools / max_rounds / agent_name）
  build_system_prompt                        拼 system prompt（提示词 + 人设 + Skills）
  resolve_whitelist                          把勾选的工具分成「可调用 / 被挡下」
  build_tool_schemas                         生成给模型的 tools 参数
  _invoke_parallel                           并发执行工具（每个任务独立 session）
  run_react_turn                             主循环本体
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoints import (
    PendingApproval,
    TurnCheckpoint,
    register_pending,
    release_pending,
    wait_for_decision,
)
from app.agent.notes import (
    Notes,
    Ref,
    normalize_notes,
    project_notes,
    render_window_digest,
    verify_notes,
)
from app.agent.prompts import DEFAULT_AGENT_SYSTEM, render_variables
from app.agent.tool_docs import render_tools_text
from app.core.security import decrypt
from app.db.models import Agent, Message, ModelConfig, Prompt, RunNote, Skill, Tool, ToolCall
from app.llm.client import LLMClient, ToolCallRequest
from app.obs.langfuse import observation as trace_span
from app.tools.registry import build_tool_schema, execute_tool, get_impl, impl_status

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

#: 文本协议里的 Action 标记。模型实测会写成 `Action: {...}`、`**Action**：{...}`，
#: 也会把 JSON 单独包进代码围栏：
#:     **Action**：
#:     ```json
#:     {"tool": "...", "input": {...}}
#:     ```
#: 只认"冒号后面紧挨着花括号"那一种会漏解析——而漏解析的代价不是"少调一次工具"，
#: 是**这一轮直接被当成最终回答**：整段裸协议连同 JSON 一起进了答案气泡（实测踩过）。
_ACTION_RE = re.compile(r"\*{0,2}Action\*{0,2}\s*[:：]", re.S)

#: Action 冒号与 JSON 之间允许出现的装饰：空白、代码围栏反引号、`json` 语言标注。
_ACTION_DECOR_RE = re.compile(r"[\s`]*(?:json)?[\s`]*", re.I)

#: 开头的 `Thought:` / `思考：` 标签（允许被代码围栏或加粗包裹）。
_THOUGHT_LABEL_RE = re.compile(
    r"^[\s`]*(?:json)?[\s`]*\*{0,2}(?:Thought|思考)\*{0,2}\s*[:：]\s*", re.I
)

#: 换页后注入的续跑指令。三句话都在防同一件事：模型看到干净上下文时，
#: 会把笔记里描述的"已完成步骤"当成"还没做"，于是从头再查一遍。
ROLLOVER_INSTRUCTION = (
    "【窗口硬切换完成】历史对话与所有工具返回结果已全部归档存储，当前上下文仅保留结构化任务笔记。\n"
    "请严格按以下规则推进任务：\n\n"
    "一、行动总则\n"
    "1. 优先读取 plan 中的待办事项推进任务，progress 中标记已完成的步骤禁止重复执行；\n"
    "二、按需召回规则（recall 工具使用边界）\n"
    "✅ 仅在以下三类场景触发召回：\n"
    "   -重点：需要引用精确原文：如简历原句、JD 任职要求原文、工具输出的完整数据/结论或者其他工具调用结果；\n"
    "   - 笔记信息存疑、出现矛盾或缺失关键细节，必须追溯原始凭证核对。\n"
    "❌ 以下场景禁止召回：\n"
    "   - 笔记中已有明确结构化结论的常规推理，禁止重复调取原文；\n"
    "   - 仅为复述已完成结论、确认常识性信息的，禁止召回；\n"
    "   - 禁止批量召回全量历史记录，每次仅召回当前步骤必需的最少指针。\n\n"
    "三、召回操作规范\n"
    "1. 调用 recall 工具，传入 refs 中对应的指针（格式：W<n>.<m>）精准定位单条记录；\n"
    "2. 召回的原始内容仅用于本轮推理，结论提炼更新进笔记后，下一轮不再携带；\n"
    "3. 涉及数据指标、原文引用、规则条款的内容，必须召回核对后再输出，禁止凭笔记摘要臆测。\n\n"
)

#: 喂给投影的单条工具结果上限。投影输入要**够准**而不是够短：
#: LLM 类工具（面试官 / 终裁）的真实输出只有 1~2k 字，全给也就几百 token；
#: 而只给"给 UI 看的 200 字预览"会让投影把省略号误判成「内容被截断」，
#: 写进笔记后再由主模型照着执行——实测一整个回合都在反复回查、重跑同一个面试官工具。
DIGEST_EXCERPT_CHARS = 1500

#: 一个回合最多换几次页。硬上限，保证回合一定走得完：
#: 换页会丢弃本轮已决定的动作（模型要带着新笔记重新决策），窗口设得太小时
#: 会变成"取回原文→换页丢弃→再取回"的活锁。到达上限就本回合停用换页。
MAX_ROLLOVERS_PER_TURN = 8

#: 回查预算占窗口的比例（D58）。**写死不做配置项** —— 它是"一次回查最多能吃掉
#: 多少窗口"的安全阀。50% 意味着回查落地后 prompt 约 55%~65%，仍在换页阈值下方，
#: 不会立刻触发换页；75% 会导致"回查完下一轮必换页"，等于每次大回查只买到一次 think。
RECALL_BUDGET_RATIO = 0.5

#: token → 字符的换算（中文大约 1 字符 ≈ 0.6 token）。
#: 这是全项目**唯一**一处换算：窗口阈值按 token 算，而 recall 按字符截。
#: 换算方向反了就是近三倍误差，而且是静默的 —— 所以集中在这一处并配了单测。
TOKEN_PER_CHAR = 0.6

#: 单条工具观察**回灌**给模型的上限（字符）。超了不是硬切，而是换成"摘要 + 真实字数 +
#: 回查指针"的合法 JSON —— 硬切的产物是半截 JSON，模型只会误判成工具坏了。
MAX_OBSERVATION_CHARS = 12000


def window_tool_names_of(window_digest: list[dict[str, Any]]) -> set[str]:
    """本窗口里"真的跑过、而且留下了指针"的工具名。

    `normalize_notes` 用它挡住最严重的一种编造：投影把模型的**思考**
    （"接下来问技术面"）记成**已完成**（"interview_hr 判定硬门槛符合"），
    而那个工具压根没被调用。
    """
    return {
        str(item.get("tool"))
        for item in window_digest
        if item.get("kind") == "tool" and item.get("pointer") and item.get("tool")
    }


async def session_next_window(db: AsyncSession, session_id: int) -> int:
    """这个会话的下一个窗口号（D50）。

    改造前窗口号每个回合都从 1 重来，所以同一会话里必然有多行 `W1`，指针要靠
    run_id 才分得清。D50 起改成**会话内跨回合累计**：新回合 = 新窗口，接着会话
    历史的最大值 +1，于是 `(window_index, seq)` 在会话内天然唯一。

    从已有行的最大值推、而不是在 `sessions` 上加一列计数器：多一份状态就多一处
    会漂移的地方。改造前的老行 `window_index` 多为 1~k，所以新号接着往后走，
    **不会和老号撞车** —— 老笔记失效的原因是它的作用域变了，不是号被占了。
    """
    latest = 0
    for model in (Message, ToolCall, RunNote):
        value = await db.scalar(
            select(func.max(model.window_index)).where(model.session_id == session_id)
        )
        latest = max(latest, int(value or 0))
    return latest + 1


async def load_notes(db: AsyncSession, session_id: int) -> RunNote | None:
    """取这个会话当前有效的那条笔记行（D53/D63）。

    只认 `pointer_scope == "session"` 的行 —— 改造前的老笔记是回合级编号，在新作用域
    下必然歧义（同一会话有多行 `W1`），注进去只会让模型拿着一串注定报错的指针空转（D62）。
    """
    if session_id is None:
        return None
    row = (
        await db.execute(
            select(RunNote)
            .where(RunNote.session_id == session_id, RunNote.pointer_scope == "session")
            .order_by(RunNote.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return row if row is not None and row.content else None


async def save_notes(
    db: AsyncSession,
    *,
    session_id: int,
    run_id: int | None,
    window_index: int,
    notes: Notes,
) -> None:
    """就地覆盖这个会话的笔记（D63）。一个会话只保留一行，不追加版本。

    版本历史改由 Langfuse 承担（`memory-rollover` / `turn-projection` 两个 span 的
    output 里都带着当次的笔记），所以这里覆盖不会把排查线索弄丢。
    """
    row = (
        await db.execute(
            select(RunNote)
            .where(RunNote.session_id == session_id)
            .order_by(RunNote.id.desc())
            .limit(1)
        )
    ).scalars().first()
    payload = notes.model_dump()
    if row is None:
        db.add(
            RunNote(
                run_id=run_id or 0,
                session_id=session_id,
                window_index=window_index,
                content=payload,
                source="projection",
                pointer_scope="session",
            )
        )
    else:
        row.run_id = run_id or row.run_id
        row.window_index = window_index
        row.content = payload
        row.source = "projection"
        # 老行被就地改写之后，它记的就是会话级编号了 —— 这一步同时完成 D62 的过滤依据切换
        row.pointer_scope = "session"
    await db.commit()


async def refine_notes(
    db: AsyncSession,
    candidate: Notes,
    previous: Notes | None,
    *,
    session_id: int,
    tool_names: set[str],
    window_digest: list[dict[str, Any]],
) -> tuple[Notes, int, int]:
    """把投影结果修成可用形态。返回 `(修好的笔记, 丢掉的 refs 数, 丢掉的非工具步骤数)`。

    换页投影和回合收尾投影（D52）共用这一套 —— 两处都做同样的事，分开写迟早会漂移。
    """
    refs_before = len(candidate.refs)
    steps_before = len(candidate.progress.stage)
    candidate = normalize_notes(
        candidate,
        previous,
        tool_names=tool_names,
        window_tool_names=window_tool_names_of(window_digest),
    )
    dropped_steps = steps_before - len(candidate.progress.stage)
    dropped_refs = refs_before - len(candidate.refs)
    dropped_refs += await prune_dead_refs(db, candidate, session_id)
    return candidate, dropped_refs, dropped_steps

async def prune_dead_refs(db: AsyncSession, notes: Notes, session_id: int | None) -> int:
    """把指向**不存在的存档行**的 refs 丢掉，返回丢掉几条。

    为什么需要它：投影会从窗口记录里"抄编号"。回查结果里若出现 `未命中：W1.T5`，
    它就把 `W1.T5` 当成有效指针抄进 refs —— 而那一行根本不存在。
    下游模型照着 refs 去回查，永远查不到，于是开始**猜编号**（实测连续 11 次 recall、
    3 次报错）。提示词已经禁止写"没查到"的指针，这里再兜一道底：
    **refs 里只允许留活指针**。

    作用域是**会话**（D50，原先按 run_id）—— 与 `recall` 保持一致，否则跨回合的
    消息/工具指针会被这里当成死指针删掉，笔记刚记下的东西下一轮就没了。
    """
    if session_id is None or not notes.refs:
        return 0
    from app.agent.notes import POINTER_RE

    kept: list[Ref] = []
    for ref in notes.refs:
        match = POINTER_RE.match(ref.pointer or "")
        if match is None:
            continue
        window, kind, seq = int(match.group(1)), match.group(2), int(match.group(3))
        model = Message if kind == "M" else ToolCall
        hit = (
            await db.execute(
                select(model.id)
                .where(
                    model.session_id == session_id,
                    model.window_index == window,
                    model.seq == seq,
                )
                .limit(1)
            )
        ).first()
        if hit is not None:
            kept.append(ref)
    dropped = len(notes.refs) - len(kept)
    notes.refs = kept
    return dropped


def digest_entry_text(observation: dict[str, Any]) -> str:
    """挑出投影应该看的内容：优先工具的真实输出，其次才是摘要。

    `observation["summary"]` 是给前端和列表用的预览（LLM 类工具是 `前 200 字 + …`），
    直接拿它当投影输入就会让投影以为内容被截断。这里改取 `data.text`（工具的真实正文），
    结构化结果（如检索工具的 `data.items`）则序列化整个 `data`，并在结尾标出**真实总字数**——
    否则只回退到 19 个字的一句话摘要，投影会把它读成"这个结果只有 19 字"（实测踩过：
    模型因此以为检索内容丢了，又去回查一遍）。
    """
    data = observation.get("data")
    if isinstance(data, dict) and str(data.get("text") or "").strip():
        # LLM 类工具（面试官 / 终裁）：正文就是 data.text
        body = str(data["text"]).strip()
    elif data:
        # 结构化结果（检索命中列表等）：整体序列化，别只留一句摘要
        body = json.dumps(data, ensure_ascii=False)
    else:
        body = str(observation.get("summary") or "")
        body = body.strip()
    if not body:
        return ""
    if len(body) > DIGEST_EXCERPT_CHARS:
        return (
            f"{body[:DIGEST_EXCERPT_CHARS]}…"
            f"[此处仅摘录，完整结果共 {len(body)} 字，需要明细请按指针回查]"
        )
    return f"{body}（完整结果共 {len(body)} 字）"


def _extract_json_object(text: str, start: int) -> str | None:
    """从 `{` 开始做花括号配对，返回完整的 JSON 片段（跳过字符串里的括号）。"""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_text_action(text: str) -> tuple[str, dict[str, Any]] | None:
    """解析文本协议里的 `Action: {"tool": ..., "input": {...}}`。

    文本协议是给不支持 function calling 的模型用的（提示词里明确要求模型输出
    Thought/Action）。运行时解析出 Action 后，走和执行原生 tool_calls 完全一样的执行器，
    再把结果以 `Observation:` 回灌。解析不出来返回 None，由调用方按普通回答处理。

    容忍写法差异（见 `_ACTION_RE`）：`**Action**`、全角冒号、以及把 JSON 单独包进
    ```json 围栏。只认最规矩那一种的代价太大——漏解析会让整轮变成"最终回答"。
    """
    if not text:

        return None
    match = _ACTION_RE.search(text)
    if match is None:
        return None
    # 跳到 JSON 的左花括号：中间只允许空白 / 反引号 / `json` 语言标注这类装饰
    start = _ACTION_DECOR_RE.match(text, match.end()).end()
    if start >= len(text) or text[start] != "{":
        return None
    raw = _extract_json_object(text, start)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.info("文本协议的 Action 不是合法 JSON，按普通回答处理")
        return None
    if not isinstance(data, dict):
        return None
    tool = str(data.get("tool") or "").strip()
    if not tool:
        return None
    arguments = data.get("input")
    return tool, arguments if isinstance(arguments, dict) else {}


def strip_protocol_scaffolding(text: str) -> str:
    """剥掉文本协议的脚手架，只留下人看的正文。

    文本协议下 `result.content` 是模型的原样输出，长这样：

        Thought: 先读简历正文。
        Action: {"tool": "pdf_extract", "input": {...}}

    这段文本有两个去处必须是干净的：前端「思考」卡片、以及模型没给出工具调用时
    兜底成的那条最终回答。不剥的话用户看到的就是裸协议 + 一段 JSON——而按约定
    这个页面只展示一种形态：💭思考 → 🔧行动 → 👁观察 的链路。

    只做减法，不重排：没有 Action、也没有 Thought 标签时原样返回。
    """
    if not text:
        return ""
    body = text
    # 1) Action 连同它后面的 JSON 一起砍掉（围栏、换行都在它后面）
    match = _ACTION_RE.search(body)
    if match is not None:
        body = body[: match.start()]
    # 2) 去掉开头的 `Thought:` / `**Thought**：` / `思考：` 标签
    body = _THOUGHT_LABEL_RE.sub("", body)
    # 3) 清掉可能残留的代码围栏与首尾空白
    body = body.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    if body.endswith("```"):
        body = body[:-3]
    return body.strip().strip("`").strip()


@dataclass
class Step:
    """一条「思考 / 行动 / 观察」。

    它是前端的渲染单位，也是落库的最小记录：一次 ReAct 回合产生若干个 Step，
    按顺序塞进 assistant 消息的 `content.steps` 里（同时通过 SSE 实时外发）。

    三种类型各自用到的字段：
      thought     → text
      action      → tool / tool_display / arguments / call_id
      observation → tool / tool_display / summary / status / tool_call_id / call_id
    """

    #: 步骤类型。只有三个取值：`thought`（模型说要干什么）/ `action`（调了哪个工具）
    #: / `observation`（工具返回了什么）。前端按它决定图标与排版。
    type: str

    #: 思考的正文字；或行动前的自言自语。仅 `thought` 会写。
    #: 太长的会被截断（见 run_react_turn 里的 `[:600]`），避免撑爆消息表。
    text: str = ""

    #: 工具的**内部标识**（tools 表里的 name，例如 `kb_search_perf`）。
    #: 前端用它在步骤里做小字标注，也是排查"到底调了哪个工具"的依据。
    tool: str = ""

    #: 工具的**显示名**（例如「招聘语料检索」）。给用户看的中文名，
    #: 取自 tools 表的 display_name，为空时前端回退显示 tool。
    tool_display: str = ""

    #: 模型这次传给工具的**参数**（已解析成 dict）。`action` 用它展示参数摘要。
    #: 注意：这里存的是模型给的值，不是工具实际收到的完整入参（后者要合并页面配置，
    #: 想看完整入参请展开 observation 的"查看完整参数与结果"）。
    arguments: dict[str, Any] = field(default_factory=dict)

    #: `tool_calls` 表的主键。前端点「查看完整参数与结果」时按这个 id 懒加载。
    #: 只有真正执行过的工具（走了数据库记录的）才有值；被白名单挡下的没有。
    tool_call_id: int | None = None

    #: 模型给的调用 id（原生协议是 `call_xxx`，文本协议是 `text-<轮次>`）。
    #: 回灌观察结果时要用它对上号（OpenAI 的 tool 消息必须带 tool_call_id）。
    call_id: str = ""

    #: 观察结果的**一句话摘要**（工具返回的 `summary` 字段），最多 500 字。
    #: 完整结果不在这里——它落在 tool_calls 表，避免把大 JSON 写进消息表。
    summary: str = ""

    #: 观察的状态：`ok` 成功 / `error` 工具报错 / `rejected` 用户拒绝了人工确认
    #: / `pending_approval` 正在等批准。前端据此显示「出错」「已拒绝」标记。
    status: str = ""

    #: 工具执行耗时（毫秒）。目前只在并发执行那条路径上填写。
    elapsed_ms: int = 0

    def dump(self) -> dict[str, Any]:
        """转成可 JSON 序列化的 dict（SSE 外发 + 落库都用它）。"""
        return {
            "type": self.type,
            "text": self.text,
            "tool": self.tool,
            "tool_display": self.tool_display,
            "arguments": self.arguments,
            "tool_call_id": self.tool_call_id,
            "call_id": self.call_id,
            "summary": self.summary,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
        }


# ---------------------------------------------------------------- 上下文组装


async def build_agent_llm(db: AsyncSession, agent: Agent) -> LLMClient:
    """按 Agent 的配置造一个 LLM 客户端，供主循环使用。

    两条路：
      A. Agent 绑定了模型配置（页面选过）→ 用那条配置：地址 / 密钥 / 模型名 / 参数 / 超时；
      B. 没绑定 → 退回 `.env` 里的默认模型，保证"没配也能跑起来"。

    注意密钥：数据库里存的是 Fernet 密文，这里 `decrypt()` 解密后才交给客户端，
    明文只活在内存里，不会回传给前端。
    """
    # 取 Agent 绑定的模型配置行；没绑定就是 None（等价于"用 .env 默认"）
    model_row = await db.get(ModelConfig, agent.model_config_id) if agent.model_config_id else None
    if model_row is None:
        # 兜底路径：读 .env 的 DEEPSEEK_* / DEFAULT_CHAT_MODEL
        from app.core.config import settings

        return LLMClient(
            base_url=settings.deepseek_base_url,          # 兼容 OpenAI 协议的地址
            api_key=settings.deepseek_api_key,            # .env 里的明文 key
            model=settings.default_chat_model,            # 例如 deepseek-chat
            params=agent.params_override or {},           # Agent 页配的"参数覆盖"
        )
    return LLMClient(
        base_url=model_row.base_url,                      # 页面配的 Base URL
        api_key=decrypt(model_row.api_key_cipher),        # 密文 → 明文（只在内存）
        model=model_row.model_name,                       # 页面配的模型名
        # 参数合并顺序：模型默认参数 → 被 Agent 的覆盖值盖住（后者优先）
        params={**(model_row.default_params or {}), **(agent.params_override or {})},
        timeout_s=model_row.timeout_s,                    # 单次调用的超时（秒）
    )


async def collect_prompt_variables(db: AsyncSession, agent: Agent) -> dict[str, str]:
    """提示词可用变量的统一来源。

    - `{{tools}}`      ← 该 Agent 勾选且已实现的工具名，去 app/agent/tool_docs.py 取写死的说明文本
    - `{{max_rounds}}` ← Agent 配置的轮数上限
    - `{{agent_name}}` ← Agent 名称

    工具说明写在代码里（tool_docs.py），不读 tools 表的 description：
    表里那份是给 function calling 的机器契约，长度和结构都受限，表达不了
    "多久、要不要确认、和别的工具的先后关系"这些提示词才需要的策略信息。

    渲染规则见 `prompts.render_variables`：认不出的变量**原样保留并告警**，
    不静默替换成空串——否则模型会以为"这条信息本来就不存在"。
    """
    # 先筛出"这个 Agent 真正能用的工具"：勾选了 + 启用 + 没被删 + 后端有实现
    tool_rows: list[Tool] = []
    for tool_id in agent.tool_ids or []:            # tool_ids 是页面多选的 id 列表
        row = await db.get(Tool, tool_id)
        # 四个条件缺一不可：行存在、is_enabled、没软删、impl_key 能在代码里找到
        if row is not None and row.is_enabled and row.deleted_at is None and get_impl(row.impl_key):
            tool_rows.append(row)
    return {
        # 工具说明文本：按工具名去 tool_docs.py 取（写死在代码里，改完直接生效）
        "tools": render_tools_text([row.name for row in tool_rows]),
        # 轮数上限：提示词里写 {{max_rounds}} 时会被替换成这个数字
        "max_rounds": str(agent.max_rounds or 12),
        # Agent 名称：让提示词能自称（"你是{{agent_name}}"）
        "agent_name": agent.name or "",
    }


async def build_system_prompt(
    db: AsyncSession,
    agent: Agent,
) -> str:
    """组装 system prompt：提示词（渲染变量）+ 人设 + Skills。

    工具既通过 function calling 的 `tools` 参数给模型（机器可解析的契约），
    也通过 `{{tools}}` 变量渲染进提示词（schema 表达不了的策略：必填、返回结构、超时、
    是否需要人工确认、依赖顺序）。两处都从 `tools` 表渲染，所以描述只有一份真相。

    拼接顺序（后者不断追加，越靠后对模型的影响往往越直接）：
        ① 页面选的 Prompt（或内置默认提示词）  →  ② 变量渲染  →  ③ 人设覆盖  →  ④ Skills
    """
    # ① 取 Agent 绑定的 Prompt 行；没绑定就 None（下面会退回内置默认）
    prompt_row = await db.get(Prompt, agent.prompt_id) if agent.prompt_id else None
    base = ""
    if prompt_row and prompt_row.messages:
        # messages 是 [{role, content, order}] 结构，这里只取 system 那一条当主体；
        # user 那条是"有模型工具"渲染模板时才用（见 tools/_llm_runner.prepare_llm）
        base = next(
            (str(m.get("content")) for m in prompt_row.messages if m.get("role") == "system"), ""
        )
    if not base:
        # 兜底：Agent 没配 Prompt、或那条 Prompt 没有 system 内容时用代码里的默认提示词
        base = DEFAULT_AGENT_SYSTEM

    # 变量渲染对「默认提示词」和「页面选的提示词」一视同仁：
    # 此前只有默认提示词会替换 {{max_rounds}}，页面里写的 {{max_rounds}} 会原样发给模型。
    # ② 把 {{tools}} / {{max_rounds}} / {{agent_name}} 换成真实值
    base = render_variables(base, await collect_prompt_variables(db, agent))

    # ③ 人设覆盖：追加在最后，用于快速改口吻（例如"更毒舌一点"），不改动原提示词
    if agent.persona:
        base = f"{base}\n\n【人设覆盖】\n{agent.persona}"

    # ④ Skills：每个 Skill 是一段 markdown 指令（如"看到 Redis 必须追问四个场景"），
    #    拼接成独立小节。只取启用且写了内容的，顺序按 agent.skill_ids 的勾选顺序。
    skill_rows = [await db.get(Skill, sid) for sid in (agent.skill_ids or [])]
    skills = [row for row in skill_rows if row is not None and row.is_enabled and row.instructions]
    if skills:
        base += "\n\n" + "\n\n".join(
            f"【Skill：{row.name}】\n{row.instructions}" for row in skills
        )
    return base


def resolve_whitelist(tools: list[Tool]) -> tuple[list[Tool], list[Tool]]:
    """把 Agent 勾选的工具分成 (可调用, 被挡下)。

    页面勾选的就是**白名单**：后端只会执行这一批里"已实现"的工具。
    没勾的、没实现的、停用的，模型既看不到也调不动。

    为什么要有"被挡下"这一组：不是为了报错，而是为了**可观测**——
    Agent 页勾了个后端没实现的工具时，要能在日志里说清楚"它被挡下了"，
    否则现象只是"模型从不调这个工具"，很难查。
    """
    # 可调用：impl_status == "ok"，即 impl_key 能在 app/tools/ 里找到对应实现
    runnable = [tool for tool in tools if impl_status(tool) == "ok"]
    # 被挡下：impl_key 为空（页面没选实现）或在代码里找不到（未实现）
    blocked = [tool for tool in tools if impl_status(tool) != "ok"]
    return runnable, blocked


def build_tool_schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    """生成传给模型的 `tools` 参数 —— 这是模型的**唯一工具视图**。

    注意两点：
    1. 只收录"已实现"的工具（`impl_status == "ok"`）。未实现的调了必然失败，
       不如从一开始就不给模型看见。
    2. 参数 schema 取自**页面**那份（`tool.params_schema`），因为页面是权威；
       代码里的 `@tool(params_schema=...)` 只是新建工具时带出的默认值。
    """
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        if not tool.is_enabled or tool.deleted_at is not None:
            continue
        if get_impl(tool.impl_key) is None:
            continue
        schemas.append(build_tool_schema(tool))
    return schemas


async def _invoke_parallel(
    call: Any,
    tool_id: int,
    llm: LLMClient,
    session_id: int | None = None,
    run_id: int | None = None,
    recall_budget_chars: int = 0,
) -> tuple[Any, dict[str, Any], str, int]:
    """并发执行用：每个任务开自己的 session。

    `AsyncSession` 不是并发安全的（一个协程用连接时另一个不能抢），所以并发跑工具时
    必须各自开会话，并在自己的会话里重新加载工具行。

    返回值是 `(call, 观察结果, 状态, 耗时毫秒)` —— 由调用方按原始顺序写回数据库与 SSE，
    这样"并发执行"不会打乱用户看到的步骤顺序。
    """
    from app.db.session import SessionLocal  # 函数内导入，避免模块级循环依赖

    started = time.perf_counter()  # 记开始时间，用来算这次工具花了多久
    async with SessionLocal() as tool_db:  # 独立会话：并发任务不能共用主会话
        # 在**自己的会话里重新加载**工具行——主协程的 ORM 对象不能跨会话用
        tool = await tool_db.get(Tool, tool_id)
        if tool is None:
            # 工具在排队期间被删了：当成一次失败的观察结果，不抛异常
            return call, {"ok": False, "summary": "工具已不存在"}, "error", 0
        # 真（正的执行入口与测试场共用）；fallback_llm 让"没单独配模型的工具"复用 Agent 的模型
        observation = await execute_tool(
            tool_db,
            tool=tool,
            arguments=call.arguments,
            fallback_llm=llm,
            session_id=session_id,
            run_id=run_id,
            recall_budget_chars=recall_budget_chars,
        )
    # 工具自己返回 ok=False（配置不全、参数缺失、执行报错…）时记为 error，供前端标红
    status = "ok" if observation.get("ok", True) else "error"
    # 把 call 一起返回：调用方按原始顺序写库/推送，保证并发不打乱用户看到的步骤顺序
    return call, observation, status, int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------- 主循环


@dataclass
class TurnResult:
    text: str
    steps: list[dict[str, Any]]
    rounds: int
    tool_call_ids: list[int]
    #: 回合结束时所在的窗口号；落库时写给这条 assistant 消息（D48 存档坐标）
    window_index: int = 1
    #: 这条 assistant 消息在窗口内的序号；指针 `W<window_index>.M<seq>` 靠它定位
    seq: int = 0



# 【上次回合落库的】messages 表：user(#121) + assistant(#122, 含 text+steps)
#                              ＋ run_notes 里那**一条**任务笔记（D63 就地覆盖）
#                                     ↓ D53：注入笔记，不再拼"最近 8 条聊天记录"全文
# 【本次回合开跑】内存 messages：system + 笔记 + 本轮 user
#                                     ↓ 逐轮追加（不落库）
#                                assistant(tool_calls) + tool(完整结果)
#                                assistant(tool_calls) + tool(完整结果)
#                                ...
#                                assistant(最终文本)
#                                     ↓ 回合结束
# 【落库】messages 表：本轮 user + 本轮 assistant（text + steps，工具结果只留摘要）
# 【落库】run_notes：回合收尾投影一次（D52），就地覆盖那条笔记

#todo ： 核心链路 run_react_turn
async def run_react_turn(
    db: AsyncSession,
    *,
    session_id: int,
    run_id: int | None,
    agent: Agent,
    user_content: str,
    attachments: list[dict[str, Any]],
    emit: EmitFn,
    approval_timeout_s: float = 180,
    checkpoint: TurnCheckpoint | None = None,
    user_message_id: int | None = None,
) -> TurnResult:
    """跑完一个完整的 ReAct 回合，返回最终回答与全部步骤。

    参数里的 `emit` 是 SSE 推送函数：每产生一条步骤/一个 token 就推给前端一次，
    前端据此实时显示"思考 → 行动 → 观察"的进度。数据库写入则集中在回合结束时
    （消息 + 步骤 JSON），中间只落 `tool_calls` 表便于展开查看完整参数与结果。

    `user_message_id` 是本轮用户消息在 messages 表里的主键（由 sessions_router 先落库再传进来）。
    它用来给这条消息盖**存档坐标**（`window_index` + `seq`），否则笔记里的 `W1.M1` 指针永远查不到东西。

    **D53：这里不再收 `history`**。原来每轮会把"最近 8 条聊天记录"的全文拼进上下文，
    每回合重复付一大段 token；现在换成"注入本会话最新的那一条任务笔记"，原文按指针回查。
    """
    # ── 1. 解析白名单：页面勾选的工具里，哪些真的能调 ──────────────────────
    tool_rows: list[Tool] = []
    for tool_id in agent.tool_ids or []:
        tool = await db.get(Tool, tool_id)
        if tool is not None and tool.is_enabled and tool.deleted_at is None:
            tool_rows.append(tool)
    runnable = [row for row in tool_rows if impl_status(row) == "ok"]
    skipped = [row for row in tool_rows if impl_status(row) != "ok"]
    tools_by_name = {tool.name: tool for tool in runnable}
    #: 只有这里面的名字才允许执行；模型要是调了别的，直接判死（见下面的白名单分支）
    whitelist = set(tools_by_name)

    # ── 2. 组装上下文：模型客户端 + system prompt + 任务笔记 + 本轮输入 ──────
    llm = await build_agent_llm(db, agent)
    system_prompt = await build_system_prompt(db, agent)
    if skipped:
        logger.info(
            "Agent %s 有 %s 个工具被白名单挡下（未实现/未指定）：%s",
            agent.name,
            len(skipped),
            "、".join(tool.name for tool in skipped),
        )

    # D53/D61：把本会话最新的那条笔记作为"此前发生了什么"注入，替掉原来 8 条聊天记录。
    # D61 那句说明是必需的：用户可能在同一个会话里换话题，而系统无从判断（投影发生在
    # 回合收尾，那时下一轮的问题还不存在）—— 唯一同时看到笔记和新问题的角色是主模型。
    carried_row = await load_notes(db, session_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if carried_row is not None:
        messages.append(
            {
                "role": "system",
                "content": (
                    "【本会话此前的任务笔记】\n"
                    f"{json.dumps(carried_row.content, ensure_ascii=False, indent=2)}\n\n"
                    "说明：以上是**本会话此前几轮**留下的任务状态，不是用户本轮提出的要求。"
                    "如果用户这次说的与它无关（换了话题），请忽略它、按新任务重新规划；"
                    "如果相关，先读 progress，别重复已经做完的事。"
                    "笔记里只有要点，需要原文时用 recall 按指针回查"
                    "（`W<窗口>.M<序号>` 是对话消息，`W<窗口>.T<序号>` 是工具结果）。"
                ),
            }
        )

    user_text = user_content
    if attachments:
        lines = [
            "【本轮附件】",
            *[
                f"- {a['filename']}（路径：{a['path']}，{a['size']} 字节）"
                for a in attachments
            ],
            "如需读取简历正文，请调用相应工具提取。",
        ]
        user_text = (user_text + "\n\n" + "\n".join(lines)).strip()
    messages.append({"role": "user", "content": user_text})

    schema_tools = build_tool_schemas(runnable)
    steps: list[Step] = []
    tool_call_ids: list[int] = []
    max_rounds = agent.max_rounds or 12
    final_text = ""
    #: 最终回答在窗口里的序号。正常收尾时下面会为它占一个号（digest 里 kind="answer"
    #: 那条），撞到轮次上限时没占 —— 由 return 处兜底补一个。
    answer_seq = 0
    #: 本轮新增消息的起点（只影响 Langfuse 上报，见 client._report_generation）
    new_from = 0
    # 本回合的"确认记忆"——生命周期就是**这一次对话回合**，下一轮用户消息进来就重置。
    # 有 checkpoint 时直接复用它的集合（那样刷新页面也能续），没有就用局部变量。
    # 确认过的工具不再问，取消过的工具直接拒（避免"取消→模型换参数再来→又弹卡片"的骚扰循环）。
    approved_tools: set[str] = checkpoint.approved_tools if checkpoint is not None else set()
    cancelled_tools: set[str] = checkpoint.cancelled_tools if checkpoint is not None else set()

    # ── 长上下文治理（D48）：窗口换页 + 外部记忆 ──────────────────────────
    # 窗口 = 模型当前能看到的上下文。快满时整段换掉，任务连续性靠笔记 + 存档指针。
    #: 换页阈值（token）。0 表示不启用（模型没配 context_window 时）。
    window_limit = 0
    recall_budget_chars = 0
    if agent.model_config_id:
        _model = await db.get(ModelConfig, agent.model_config_id)
        if _model is not None and int(_model.context_window or 0) > 0:
            pct = int(getattr(agent, "context_threshold_pct", 80) or 80)
            window_limit = int(_model.context_window) * max(min(pct, 95), 10) // 100
            #: 回查预算（D58）：窗口上限 × 50%，换算成**字符**交给 recall。
            #: 这是全项目唯一一处 token→字符换算（见 TOKEN_PER_CHAR 的注释）。
            recall_budget_chars = int(window_limit * RECALL_BUDGET_RATIO / TOKEN_PER_CHAR)
    #: 当前窗口号（D50）。**会话内跨回合累计**：新回合 = 新窗口，接在会话历史的最大值
    #: 之后，所以 `(window_index, seq)` 在整个会话里唯一，指针不再需要 run_id 才分得清。
    window_index = await session_next_window(db, session_id)
    #: 窗口内统一序号：每追加"一条可寻址的条目"（用户输入 / 工具调用 / 最终回答）+1
    window_seq = 0
    #: 当前笔记（物化后的任务状态）。**从上一轮留下的那条接着写**（D4 追加语义），
    #: 而不是从空的开始——否则 progress 里的"已完成步骤"每轮都会被清掉一次。
    notes: Notes | None = None
    if carried_row is not None:
        try:
            notes = Notes.model_validate(carried_row.content)
        except Exception as exc:  # noqa: BLE001 - 笔记读不回来不能挡住这一轮对话
            logger.warning("上一版笔记解析失败，本轮按空笔记开始：%s", exc)
    #: 上一轮的 prompt token 数与增量，用来预测"下一轮会不会顶到上限"
    last_prompt_tokens = 0
    last_delta = 0
    #: 本窗口的"思考流水 + 观察摘要"，作为投影的输入（**不含**工具返回的完整 JSON）
    window_digest: list[dict[str, Any]] = []
    #: 最近几轮的工具调用签名，用于低效触发（连续 3 轮完全相同 = 重复试错）
    round_signatures: list[str] = []
    #: 模型在本窗口**最后一次 think 的原文**。换页时单独喂给投影，
    #: 让笔记里的 `plan` 直接取它——而不是让投影器自己编一个计划。
    latest_thought = ""
    #: 本回合已经被换页丢过的动作签名，用来识别"原地打转"（防活锁，详见换页块）。
    #: 必须是**集合**：被丢的动作和中间穿插的取原文动作会交替出现，
    #: 只比"上一次被丢的那个"永远比不上（实测踩过）。
    discarded_signatures: set[str] = set()
    #: 本回合已换页次数，硬上限见 MAX_ROLLOVERS_PER_TURN
    rollover_count = 0

    # 本轮用户消息是窗口里的第一条**可寻址**记录 —— 它由 sessions_router 先落库，
    # 这里补盖存档坐标（`W1.M1`）。不这么做的话，笔记里引用的 `W*.M*` 指针在
    # recall 里永远查不到行（messages 的这两列一直是默认值 0）。
    # 拿不到 id（老调用方）也不影响主流程，只是这条消息不能被指针回查。
    if user_message_id:
        user_row = await db.get(Message, user_message_id)
        if user_row is not None:
            window_seq += 1
            user_row.window_index = window_index
            user_row.seq = window_seq
            window_digest.append(
                {"pointer": f"W{window_index}.M{window_seq}", "kind": "user", "text": user_content[:300]}
            )
            await db.commit()

    # 换页有个**硬前提**：模型必须能按指针把原文查回来，也就是白名单里得有 `recall`。
    # 少了它，换页等于"原文彻底丢失"——实测就是这么翻车的：模型回查失败后，
    # 只能转头让用户"重新上传一次简历"。这里只告警不阻断（少一次提示好过卡死整条链路）。
    if window_limit > 0 and "recall" not in whitelist:
        logger.warning(
            "Agent #%s 启用了换页（阈值 %s tokens）但白名单里没有 recall 工具："
            "换页后模型无法回查原文。请在 Agents 页把「回查存档」勾上。",
            agent.id,
            window_limit,
        )

    # ══════════════════════════════════════════════════════════════════
    # 主循环：每一轮 = 一次 LLM 调用 + 它要求的工具调用 + 结果回灌
    # ══════════════════════════════════════════════════════════════════
    for round_index in range(1, max_rounds + 1):
        # 从 1 开始（不是 0）：这个编号会写进 tool_calls.round_index，人看"第 1 轮"更自然。
        # 循环正常跑完（没 break）说明撞到上限，由下面的 for...else 收尾。

        # 本轮开始前 messages 的长度。本轮结束会往里追加 assistant + observation，
        # 所以这条基线的含义是：**下一轮模型看到的"新增内容"从这里开始**。
        # 它只影响 Langfuse 上报（见 llm/client.py），不改变真正发给模型的上下文。
        round_baseline = len(messages)

        # 流式回调：模型每吐一个片段就推给前端，前端据此显示"正在输出"。
        # `round_no: int = round_index` 的默认参数是**故意写的**——Python 闭包捕获变量而非值，
        # 不这样绑，所有轮次都会显示最后一轮的编号。
        async def on_token(delta: str, round_no: int = round_index) -> None:
            await emit("token", {"round": round_no, "delta": delta})

        # 一次 LLM 调用。四个入参的含义：
        #   messages         完整上下文（system + 历史 + 本轮输入）——LLM 无状态，每轮都要重发
        #   tools            只传白名单里"已实现"的工具；空列表转 None，因为有的 API 不接受空数组
        #   on_token         流式回调
        #   new_from         本轮新增消息的起点，只用于 Langfuse 上报
        # 返回 ChatStepResult：content（模型说的话）/ tool_calls（它要调的工具）/ finish_reason
        result = await llm.stream_chat(
            messages,
            tools=schema_tools or None,
            on_token=on_token,
            new_from=new_from,
        )

        # 换页到底有没有生效，看这一行最直接：换页后上下文条数应该**掉回个位数**
        # （system + 笔记 + 续跑指令 + 本轮自身的 assistant/tool），prompt 也应该掉回去。
        # 不打印的话，"换了页但模型还像记得之前的事"根本没法区分是没切成还是模型在猜。
        logger.info(
            "轮 %s｜窗口 W%s｜上下文 %s 条｜prompt=%s + completion=%s = %s tokens（换页点 %s）",
            round_index,
            window_index,
            len(messages),
            result.prompt_tokens,
            result.completion_tokens,
            result.prompt_tokens + result.completion_tokens,
            window_limit or "未启用",
        )

        # ── 识别模型这轮想干什么：兼容两种协议，提示词用哪版都不会卡 ──────────
        #   1) 原生 function calling —— 模型直接返回结构化的 tool_calls（优先）
        #   2) 文本协议 —— 模型在正文里写 `Action: {"tool": "...", "input": {...}}`
        #      （给不支持 function calling 的模型；提示词里要求输出 Thought/Action）
        # 顺序不能反：原生更可靠，只有在"没有 tool_calls 但有文本"时才去猜文本协议。
        text_protocol = False
        if not result.tool_calls and result.content:
            parsed = parse_text_action(result.content)
            if parsed is not None:
                tool_name, tool_args = parsed
                if tool_name == "finish":
                    # 文本协议约定 `{"tool":"finish","input":{"answer":"..."}}` = 我收尾了。
                    # 取 answer 当最终回答；模型偶尔忘写 answer，就退回整段文本
                    # （退回时也要先剥脚手架，否则回答里会带着 `Thought:` 和 Action JSON）。
                    answer = str(tool_args.get("answer") or "").strip()
                    final_thought = strip_protocol_scaffolding(result.content)
                    final_text = answer or final_thought
                    # 收尾这一轮也要留痕：模型的最后一句"思考"就在 result.content 里，
                    # 而这里 break 得比下面的步骤记录块早——不补这一条，链路会缺最后一环，
                    # 看起来像"没思考就下了结论"。只在它是独立正文（≠回答）时补，避免复读答案。
                    if final_thought and final_thought != final_text:
                        thought = Step(type="thought", text=final_thought[:600])
                        steps.append(thought)
                        await emit("step", thought.dump())
                    break
                # 关键一步：把解析出的 Action 包装成和原生一样的 ToolCallRequest，
                # 这样下面的执行、落库、回灌全部复用同一条路径，不用写两套逻辑。
                # id 用 `text-<轮次>`：文本协议没有真正的 call id，但要保证唯一（回灌时不串）。
                result.tool_calls = [
                    ToolCallRequest(
                        id=f"text-{round_index}",
                        name=tool_name,
                        arguments=tool_args,
                        raw_arguments=json.dumps(tool_args, ensure_ascii=False),
                    )
                ]
                text_protocol = True

        # 两种协议都没给出工具调用 → 模型这轮就是在回答，整段文本即最终答案。
        # 兜底文案防"模型返回空"的极端情况，避免前端显示空白。
        if not result.tool_calls:
            # 先剥协议脚手架：模型偶尔会把 Action 写坏（JSON 不合法）导致上面的解析失败，
            # 那时 result.content 里还留着 `Thought: … Action: {...}` 整段原文，直接当回答
            # 就会让答案气泡里出现裸协议（页面只展示思考/行动/观察链路）。
            final_text = (
                strip_protocol_scaffolding(result.content)
                or result.content
                or "（模型没有返回内容）"
            )
            # D52：最终回答也要进 digest —— 它是下一轮最该被摘要的东西。
            # 改造前三处 append 只有 user/thought/tool，模型给出最终回答时代码直接 break，
            # 于是 render_window_digest 里预留的 "answer" 标签从来没人用，投影只能从
            # "过程"里猜结论。占的这个号同时也给 sessions_router 盖给 assistant 消息。
            window_seq += 1
            answer_seq = window_seq
            window_digest.append(
                {
                    "pointer": f"W{window_index}.M{window_seq}",
                    "kind": "answer",
                    "text": final_text[:300],
                    "round": round_index,
                }
            )
            break

        # 低效触发的依据：本轮调了哪些工具、参数是什么。
        # 连续几轮完全一致 = 典型的重复试错，该换页把垃圾信息清掉了。
        round_signatures.append(
            "|".join(
                sorted(
                    f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
                    for call in result.tool_calls
                )
            )
        )

        # ── 换页判断（D57）：位置在 **think 之后、act 之前** ────────────────
        # 模型刚说完要调哪些工具，但还没真正执行。选这个位置的理由：
        #   1. 本轮工具的返回会**直接落在新窗口里**。放在轮末（observation 之后）时，
        #      刚拿到的结果立刻被投影压成摘要，下一轮只能按指针回查一遍 —— 这是之前
        #      最主要的空转来源（实测一个回合 7 次回查都在搬同一批材料）。
        #   2. 判断用的 token 数来自刚结束的这次 LLM 调用的真实 usage，和放轮末一样准。
        # `latest_thought` 在这一刻就是 `result.content`（模型刚说要干什么），
        # 笔记里的 plan 直接取它。
        last_delta = (
            result.prompt_tokens + result.completion_tokens - last_prompt_tokens
            if last_prompt_tokens
            else 0
        )
        last_prompt_tokens = result.prompt_tokens + result.completion_tokens

        triggers: list[str] = []
        if window_limit > 0 and last_prompt_tokens >= window_limit:
            triggers.append("hard_threshold")  # 本轮用量已经越过阈值
        if (
            window_limit > 0
            and last_delta > 0
            and last_prompt_tokens + last_delta >= window_limit
        ):
            triggers.append("projected_overflow")  # 按上轮增量外推，下一轮就会顶到上限
        if len(round_signatures) >= 3 and len(set(round_signatures[-3:])) == 1:
            triggers.append("stalled_retry")  # 连续 3 轮工具与参数完全相同 = 重复试错

        # ── 防活锁：换页后"重新决策"不能变成"永远在原地重新决策" ─────────────
        # 换页按设计会丢掉本轮已决定的动作（让模型带着新笔记重新判断）。
        # 但窗口太小时会变成：
        #   取回原文 → 换页丢掉"调面试官" → 新窗口原文没了 → 再取回来 → 又丢掉…
        # 两条保险：
        #   1. 同一个动作**被丢过一次之后**再出现，就不再换页、直接执行
        #      （模型重新判断后还是同一件事，说明这个动作不受换页影响）；
        #   2. 一回合换页总数封顶（MAX_ROLLOVERS_PER_TURN），到顶就停用换页。
        this_signature = round_signatures[-1] if round_signatures else ""
        if triggers and this_signature and this_signature in discarded_signatures:
            logger.warning(
                "换页原地打转：这个动作已被丢过一次，本次不换页、直接执行（%s）",
                this_signature[:120],
            )
            triggers = []
        elif triggers and rollover_count >= MAX_ROLLOVERS_PER_TURN:
            logger.warning(
                "本回合换页已达上限 %s 次，停用换页让它跑完（窗口可能设得太小）",
                MAX_ROLLOVERS_PER_TURN,
            )
            await emit(
                "stage",
                {
                    "stage": "rollover_disabled",
                    "reason": f"本回合换页已达上限 {MAX_ROLLOVERS_PER_TURN} 次，先跑完这一轮",
                },
            )
            window_limit = 0
            triggers = []

        rolled_this_round = False
        if triggers:
            # 笔记的 plan 取自"最后一次 think"，同样要剥脚手架
            latest_thought = strip_protocol_scaffolding(result.content) or latest_thought
            with trace_span(
                "memory-rollover",
                as_type="span",
                input={
                    "window_index": window_index,
                    "tokens": last_prompt_tokens,
                    "window_limit": window_limit,
                    "digest_entries": len(window_digest),
                    "rounds_in_window": len(round_signatures),
                    "at": "think->act",  # 换页发生在 think 之后、act 之前
                },
                metadata={
                    "trigger": triggers,
                    "delta_tokens": last_delta,
                    "context_threshold_pct": int(
                        getattr(agent, "context_threshold_pct", 80) or 80
                    ),
                    "session_id": session_id,
                    "run_id": run_id,
                },
            ) as rollover_span:
                candidate: Notes | None = None
                problems: list[str] = []
                dropped_refs = 0
                dropped_steps = 0  # 被 normalize 丢掉的"非工具步骤"条数
                try:
                    candidate = await project_notes(
                        llm=llm,
                        previous=notes,
                        window_digest=render_window_digest(window_digest),
                        latest_thought=latest_thought,
                    )
                    # 传 whitelist 进去：`progress.stage` 只允许记"真实工具干的事"，
                    # 非工具步骤（"直接判断：…"）会被丢掉；window_tool_names 是"本窗口
                    # 真的跑过、并且留下指针的工具"，用来挡住"把思考当成已完成"这种编造。
                    # normalize + 死指针清理与回合收尾投影（D52）共用同一套，避免两处漂移。
                    candidate, dropped_refs, dropped_steps = await refine_notes(
                        db,
                        candidate,
                        notes,
                        session_id=session_id,
                        tool_names=whitelist,
                        window_digest=window_digest,
                    )
                    problems = verify_notes(candidate, notes)
                except Exception as exc:  # noqa: BLE001 - 投影失败绝不能影响主流程
                    logger.warning("笔记投影失败，本次不换页：%s", exc)
                    candidate, problems = None, ["投影调用失败"]

                if candidate is not None and not problems:
                    # D63：就地覆盖这个会话的笔记，不再追加版本行
                    await save_notes(
                        db,
                        session_id=session_id,
                        run_id=run_id,
                        window_index=window_index + 1,
                        notes=candidate,
                    )
                    notes = candidate
                    window_index += 1
                    window_seq = 0
                    window_digest = []
                    round_signatures = []
                    rolled_this_round = True
                    # 清空窗口并重建。**这一步发生在 assistant(tool_calls) 写进上下文之前**，
                    # 所以接下来那句 assistant 消息和它引发的 tool 返回都会落在新窗口里，
                    # assistant(tool_calls)/tool 的配对不会被切断（OpenAI 协议要求成对）。
                    messages.clear()
                    messages.extend(
                        [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "system",
                                "content": f"【任务笔记】\n{notes.model_dump_json(indent=2)}",
                            },
                            {"role": "user", "content": ROLLOVER_INSTRUCTION},
                        ]
                    )
                    await emit("stage", {"stage": "rollover", "window_index": window_index})
                    logger.info(
                        "上下文换页：窗口 %s（%s tokens / 上限 %s）",
                        window_index,
                        last_prompt_tokens,
                        window_limit,
                    )
                    if rollover_span is not None:
                        with contextlib.suppress(Exception):
                            rollover_span.update(
                                output={
                                    "rolled": True,
                                    "new_window_index": window_index,
                                    "note": candidate.model_dump(),
                                    "dropped_refs": dropped_refs,
                                    "dropped_non_tool_steps": dropped_steps,
                                    # 换页后**不执行**本轮已决定的动作：模型带着新笔记重新决策
                                    "executed_pending_actions": False,
                                }
                            )
                    # ★ 换页后让模型**带着新笔记重新决策**，而不是照着旧上下文里定好的动作往下跑。
                    # 这正是"按需回查"能发生的前提：模型先读到新笔记，判断缺什么原文，
                    # 再用 refs 里的指针去 recall。丢掉本轮的 tool_calls（continue），
                    # 下一轮它会重新想一遍。
                    rollover_count += 1
                    if this_signature:
                        discarded_signatures.add(this_signature)
                    continue
                else:
                    # 校验不通过就不换 —— 宁可上下文继续涨，也不能带着残缺笔记进新窗口
                    logger.warning("笔记校验未通过，本次不换页：%s", problems)
                    if rollover_span is not None:
                        with contextlib.suppress(Exception):
                            rollover_span.update(
                                output={
                                    "rolled": False,
                                    "problems": problems,
                                    "dropped_refs": dropped_refs,
                                    "note": candidate.model_dump() if candidate else None,
                                }
                            )

        # 模型可能先嘀咕几句再决定调工具。这段嘀咕作为一条"思考"落库 + 推送。
        # `[:600]` 是防止它长篇大论把消息表撑爆（完整内容不需要留）。
        # 文本协议下 result.content 还带着 `Thought:` 标签和整段 `Action: {...}`，
        # 这里先剥掉，落库/推送的才是"思考正文"（否则思考卡片里显示的是裸协议 + JSON）。
        thought_text = strip_protocol_scaffolding(result.content)
        if thought_text:
            thought = Step(type="thought", text=thought_text[:600])
            steps.append(thought)
            await emit("step", thought.dump())
            # 这一段就是"act 之前的 think"：模型刚说完要做什么，紧接着才调工具。
            # 笔记的 plan 要的就是它。
            latest_thought = thought_text
            # 投影输入：思考流水（不带指针——它只体现"模型当时的判断"，不需要回查原文）
            # 带上轮次：投影时 `decisions[].round` 就靠它，不写模型只能瞎猜。
            window_digest.append(
                {
                    "pointer": "",
                    "kind": "thought",
                    "text": thought_text[:300],
                    "round": round_index,
                }
            )

        # ── 把这一轮的 assistant 回复写进上下文（下一步回灌观察结果的前提）──────
        # 两种协议的写法不同，但作用一样：把"模型说了什么/要调什么"记进历史。
        if text_protocol:
            # 文本协议：Thought/Action 本来就是一段纯文本，直接当普通 assistant 消息
            messages.append({"role": "assistant", "content": result.content})
        else:
            # 原生协议：必须带上 tool_calls 数组——OpenAI 要求 assistant 消息与随后的
            # `role="tool"` 消息按 id 配对，少一边会直接被 API 拒绝（400）
            messages.append(
                {
                    "role": "assistant",
                    # 纯工具调用时 content 是空串，而协议要求这种情况传 null
                    "content": result.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                # arguments 必须是 **JSON 字符串**（不是对象），
                                # raw_arguments 是模型原样返回的，优先用它以保留原始文本
                                "arguments": call.raw_arguments
                                or json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in result.tool_calls
                    ],
                }
            )

        # ── 并发预取 ────────────────────────────────────────────────────────
        # 一轮里模型可能同时调多个工具（比如技术面试官 + HR 面试官）。
        # 先把「不需要人工确认」的那几个并发跑掉，再回到下面的循环里按**原始顺序**
        # 落库与推送 —— 两个各跑 20~30 秒的工具串行就是 60 秒，并发能省掉一半。
        #: call.id → (观察结果, 状态, 耗时毫秒)
        parallel_direct: dict[str, tuple[dict[str, Any], str, int]] = {}
        # 读 Agent 页的「同轮工具并发」开关；用 getattr 兜底，老数据没有这列时按"开"处理
        if bool(getattr(agent, "parallel_tools", True)):
            # 挑出**能并发跑**的调用，两个条件缺一不可：
            #   1. 在 tools_by_name 里 —— 即白名单内且已实现（不在的等下面按序走拒绝分支）
            #   2. 不需要人工确认 —— 否则会同时弹两个确认框、且要等用户点击
            pending = [
                call
                for call in result.tool_calls
                if call.name in tools_by_name and not tools_by_name[call.name].requires_approval
            ]
            # 只有一个就不用并发了（gather 也有调度开销）
            if len(pending) > 1:
                outcomes = await asyncio.gather(
                    *[
                        # 每个任务自己开会话（AsyncSession 不是并发安全的）
                        _invoke_parallel(
                            call,
                            tools_by_name[call.name].id,
                            llm,
                            session_id=session_id,
                            run_id=run_id,
                            recall_budget_chars=recall_budget_chars,
                        )
                        for call in pending
                    ]
                )
                # 结果先存起来，不在这里写库/推送 —— 顺序交给下面的循环决定，
                # 这样"并发执行"不会打乱用户看到的步骤顺序
                for call, observation, status, elapsed_ms in outcomes:
                    parallel_direct[call.id] = (observation, status, elapsed_ms)

        # ── 逐个处理工具调用（按模型给出的顺序，保证用户看到的顺序不乱）──────
        for call in result.tool_calls:
            # 白名单判死：不在名单里就当 tool=None，走下面的拒绝分支（不进执行器）
            tool = tools_by_name.get(call.name) if call.name in whitelist else None
            # 显示名：优先中文名；工具不存在时退回模型给的名字，界面上还能看出它想调什么
            display = (tool.display_name or tool.name) if tool else call.name
            # 先推「行动」再执行：用户立刻看到"正准备调 X"，而不是盯着空白等 30 秒
            action = Step(
                type="action",
                tool=call.name,
                tool_display=display,
                arguments=call.arguments,
                call_id=call.id,
            )
            steps.append(action)
            await emit("step", action.dump())

            if tool is None:
                # 页面没勾选 / 后端没实现 / 已停用 —— 一律按白名单拒绝。
                # 这里**伪造一条观察结果回灌**，而不是抛异常：模型看到后会改用别的工具
                # 或直接作答，整个回合不会因为一次越权调用而崩掉。
                observation = {
                    "ok": False,
                    "summary": (
                        f"工具 `{call.name}` 不在本 Agent 的白名单里"
                        "（页面未勾选，或后端没有对应实现），请改用已提供的工具"
                    ),
                    "error": "not_in_whitelist",
                }
                status = "error"
            else:
                # 建 tool_calls 记录（状态 running）。立刻 commit + refresh 是为了拿到
                # 自增 id——审批流程要用它，前端"查看完整参数与结果"也按它查。
                # 完整参数与完整结果都落这张表，不塞进消息表，避免单条会话膨胀到几 MB。
                record = ToolCall(
                    session_id=session_id,
                    run_id=run_id,
                    tool_id=tool.id,
                    tool_name=tool.name,
                    round_index=round_index,
                    arguments=call.arguments,
                    requires_approval=bool(tool.requires_approval),
                    status="running",
                )
                # 存档坐标：指针 W<n>.T<m> 靠这两列定位到这次调用
                window_seq += 1
                record.window_index = window_index
                record.seq = window_seq
                db.add(record)
                await db.commit()
                await db.refresh(record)

                # 取结果的优先级，顺序不能乱：
                #   1. 并发预取已经跑完 → 直接取结果
                if call.id in parallel_direct:
                    observation, status, elapsed_ms = parallel_direct[call.id]
                    record.elapsed_ms = elapsed_ms
                #   2. 本回合已被用户**取消**过 → 不再弹卡片，直接拒绝
                elif tool.name in cancelled_tools:
                    observation = {
                        "ok": False,
                        "summary": (
                            f"用户已经取消过「{display}」，本轮不要再调用它；"
                            "请换个方式，或者基于已有信息作答"
                        ),
                        "error": "cancelled_by_user",
                    }
                    status = "rejected"
                #   3. 本回合已被用户**确认**过 → 跳过询问，直接执行
                elif tool.name in approved_tools:
                    started = time.perf_counter()
                    observation = await execute_tool(
                        db,
                        tool=tool,
                        arguments=call.arguments,
                        fallback_llm=llm,
                        session_id=session_id,
                        run_id=run_id,
                        recall_budget_chars=recall_budget_chars,
                    )
                    status = "ok" if observation.get("ok", True) else "error"
                    record.elapsed_ms = int((time.perf_counter() - started) * 1000)
                #   4. 需要人工确认 → 弹卡片并挂起等待（可配超时，返回三态）
                elif tool.requires_approval:
                    record.status = "pending_approval"
                    await db.commit()
                    # 把这次等待登记进 checkpoint：刷新页面后前端据此重建卡片，
                    # 审批接口也靠它反查到"是哪个回合在等"
                    pending = PendingApproval(
                        tool_call_id=record.id,
                        tool_name=tool.name,
                        tool_display=display,
                        tool_hint=(tool.description or "")[:120],
                        arguments=call.arguments,
                        created_at=time.monotonic(),
                        timeout_s=int(approval_timeout_s),
                    )
                    # 无条件登记：没有 checkpoint 时也要让审批接口能按 id 找到它
                    register_pending(checkpoint, pending)
                    await emit("approval_required", pending.dump())
                    # 挂起：等用户点确认/取消，或超时
                    decision = await wait_for_decision(pending)
                    release_pending(pending)
                    # 记忆规则（都只作用于**本回合**，下一条用户消息进来就重置）：
                    #   - 用户取消 → 无条件记住：本回合不再问这个工具，模型再调就直接拒
                    #     （否则会变成"取消 → 模型换参数再来 → 又弹卡片"的骚扰循环）
                    #   - 用户确认 → 只有勾了"本回合不再问我"才记住
                    if decision == "cancelled":
                        cancelled_tools.add(tool.name)
                    elif decision == "approved" and pending.remember:
                        approved_tools.add(tool.name)
                    # 把这张确认卡片**也写进步骤流**：回合结束后它随消息一起落库，
                    # 用户回看时能看到"哪些工具被确认过、哪些被取消过"。
                    # 顺序上它排在"行动"之后、"观察"之前，正好是决策发生的位置。
                    approval_step = Step(
                        type="approval",
                        tool=tool.name,
                        tool_display=display,
                        # 带上主键：前端据此把"等待中"的临时卡片替换成"已决"卡片，
                        # 而不是并排出现两张
                        tool_call_id=record.id,
                        arguments=call.arguments,
                        status=decision,
                        summary={
                            "approved": "已确认执行",
                            "cancelled": "已取消（本回合不再询问）",
                            "timeout": "等待超时，已跳过",
                        }.get(decision, decision),
                    )
                    steps.append(approval_step)
                    await emit("step", approval_step.dump())
                    record.approved = decision == "approved"
                    await db.commit()
                    if decision != "approved":
                        # 取消与超时给**不同**的措辞：超时是"没等到人"，
                        # 取消是"人明确不让做"——模型该做的反应不一样
                        observation = {
                            "ok": False,
                            "summary": (
                                "用户取消了执行，请换个方式或直接说明无法完成"
                                if decision == "cancelled"
                                else "等待用户确认超时，请换个不需要确认的做法继续"
                            ),
                            "error": decision,
                        }
                        status = "rejected" if decision == "cancelled" else "timeout"
                    else:
                        # 确认后正常执行（串行，用主会话）
                        started = time.perf_counter()
                        observation = await execute_tool(
                            db,
                            tool=tool,
                            arguments=call.arguments,
                            fallback_llm=llm,
                            session_id=session_id,
                            run_id=run_id,
                            recall_budget_chars=recall_budget_chars,
                        )
                        status = "ok" if observation.get("ok", True) else "error"
                        record.elapsed_ms = int((time.perf_counter() - started) * 1000)
                #   3. 直接执行（串行）。fallback_llm 让"没单独配模型的工具"复用 Agent 的模型
                else:
                    started = time.perf_counter()
                    observation = await execute_tool(
                        db,
                        tool=tool,
                        arguments=call.arguments,
                        fallback_llm=llm,
                        session_id=session_id,
                        run_id=run_id,
                        recall_budget_chars=recall_budget_chars,
                    )
                    status = "ok" if observation.get("ok", True) else "error"
                    record.elapsed_ms = int((time.perf_counter() - started) * 1000)

                # 统一收尾：状态 / 完整结果 / 摘要 / 错误写回。
                # 摘要与错误各截断 500 字——会话列表和步骤时间线要读它们，不能太大。
                record.status = status
                record.result = observation
                record.summary = str(observation.get("summary") or "")[:500]
                record.error = str(observation.get("error") or "")[:500]
                await db.commit()
                tool_call_ids.append(record.id)

            # 推一条「观察」给前端：只带一句话摘要，完整结果前端按 tool_call_id 懒加载。
            # ⚠️ 已知缺陷：`tool_call_ids[-1]` 取的是"最后一个已记录的工具"。当前这个调用
            # 若被白名单挡下（没建 record），它会取到**上一个工具**的 id，
            # 前端点"查看完整参数与结果"会显示错的那条。修法：本次没建 record 就传 None。
            observation_step = Step(
                type="observation",
                tool=call.name,
                tool_display=display,
                tool_call_id=tool_call_ids[-1] if tool_call_ids else None,
                call_id=call.id,
                summary=str(observation.get("summary") or "")[:500],
                status=status,
                elapsed_ms=0,
            )
            steps.append(observation_step)
            await emit("step", observation_step.dump())

            # 投影输入：观察摘要（带指针，方便笔记里引用"详情在 W2.T3"）
            window_digest.append(
                {
                    "pointer": f"W{window_index}.T{window_seq}" if tool is not None else "",
                    "kind": "tool",
                    # ⚠️ 必须带工具名：只给摘要文本的话，模型分不清「W4.T2 到底是技术面还是 HR 面」，
                    # 实测它会把两条搞混，然后在笔记里写错 refs.summary，后续窗口照着错笔记跑偏。
                    "tool": call.name,
                    # ⚠️ 必须给**真实输出**而不是给 UI 的预览：用 `summary`（前 200 字 + …）
                    # 会让投影以为"内容被截断"，笔记里写死"截断版"，主模型于是反复回查 + 重跑工具。
                    "text": digest_entry_text(observation),
                    "round": round_index,
                }
            )

            # 回灌给模型：这是 ReAct 的"观察"环节，也是下一轮模型能看到的**新信息**。
            # 大结果不能整段塞回去，但也**绝不能按字符硬切**：切出来的是半截 JSON，
            # 模型解析不了，只会误判成"工具坏了"或者把残片当成内容本身（改造前就是
            # `[...][:12000]`，切得越频繁、结果越像乱码）。这里换成一条**合法 JSON**：
            # 摘要 + 真实字数 + 回查指针，需要明细时按指针拉原文（D57 同一个思路）。
            payload_json = json.dumps(observation, ensure_ascii=False)
            if len(payload_json) > MAX_OBSERVATION_CHARS:
                recall_pointer = f"W{window_index}.T{window_seq}" if tool is not None else ""
                payload_json = json.dumps(
                    {
                        "ok": observation.get("ok", True),
                        "summary": str(observation.get("summary") or "")[:500],
                        "truncated": True,
                        "full_chars": len(payload_json),
                        "recall_pointer": recall_pointer,
                        "hint": (
                            "这条工具结果太大，没有整体回灌；summary 是它的要点。"
                            + (
                                f"需要完整明细请用 recall 取 `{recall_pointer}`。"
                                if recall_pointer
                                else "需要明细请换一种更小范围的调用方式。"
                            )
                        ),
                    },
                    ensure_ascii=False,
                )
            if text_protocol:
                # 文本协议没有 tool_call_id，用一条 `Observation: {...}` 的 user 消息回灌
                messages.append({"role": "user", "content": f"Observation: {payload_json}"})
            else:
                # 原生协议必须用 role="tool" + tool_call_id，和上面 assistant 的 tool_calls 配对
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": payload_json}
                )

        # 本轮收尾：把"新增起点"更新为本轮开始前的长度。
        # 下一轮模型看到的"新增"就是本轮追加的 assistant + observation，
        # 而它们的起点恰恰是 round_baseline（只影响 Langfuse 上报）。
        # 本轮换过页的话，上下文被重建成了 3 条（system / 笔记 / 续跑指令），
        # 增量起点要从 3 开始，否则 Langfuse 上会把整个新窗口都当成"本轮新增"重发一遍。
        new_from = 3 if rolled_this_round else round_baseline

        # 同步作业面到 checkpoint：刷新页面后前端靠 steps 重建时间线；
        # 若后面挂起等待确认，唤醒后后台任务也靠 messages 接着往下跑。
        # 注意 messages 是**引用赋值**（同一个 list），后续追加会自动反映，不必每步都同步。
        if checkpoint is not None:
            checkpoint.round_index = round_index
            checkpoint.messages = messages
            checkpoint.steps = [step.dump() for step in steps]
            checkpoint.tool_call_ids = list(tool_call_ids)

    else:
        # for...else：只有循环**自然跑完**（没有 break）才执行，即 max_rounds 轮都没得出结论。
        # 给一句明确交代，而不是返回空让用户以为出错了。
        final_text = "（已达到最大轮次，先给出当前结论）"
        await emit("token", {"round": max_rounds, "delta": final_text})

    # 最后一道兜底：万一上面所有分支都没能给出文本（例如工具全失败又没有收尾语）
    if not final_text:
        final_text = "（模型没有给出最终回答）"
        await emit("token", {"round": 0, "delta": final_text})

    # ── 回合收尾投影（D52/D60）─────────────────────────────────────────────
    # 投影原来只在换页时跑，所以**不换页的回合什么都没留下**：下个回合没有笔记可注入，
    # 只能靠"最近 8 条聊天记录"续命（D53 已经把它拿掉了）。这里再跑一次，输入就是本
    # 窗口的流水 —— 含刚补进去的那条最终回答。这是"每轮对话都有摘要"的唯一保证。
    if window_digest:
        with trace_span(
            "turn-projection",
            as_type="span",
            input={"window_index": window_index, "digest_entries": len(window_digest)},
            metadata={"session_id": session_id, "run_id": run_id, "at": "turn-end"},
        ) as turn_span:
            candidate: Notes | None = None
            problems: list[str] = []
            dropped_refs = 0
            dropped_steps = 0
            try:
                candidate = await project_notes(
                    llm=llm,
                    previous=notes,
                    window_digest=render_window_digest(window_digest),
                    latest_thought=latest_thought,
                )
                candidate, dropped_refs, dropped_steps = await refine_notes(
                    db,
                    candidate,
                    notes,
                    session_id=session_id,
                    tool_names=whitelist,
                    window_digest=window_digest,
                )
                problems = verify_notes(candidate, notes)
            except Exception as exc:  # noqa: BLE001 - 收尾投影失败绝不能影响这一轮的回答
                logger.warning("回合收尾投影失败（本轮回答不受影响）：%s", exc)
                candidate, problems = None, ["投影调用失败"]
            if candidate is not None and not problems:
                # D63：就地覆盖，不追加版本行
                await save_notes(
                    db,
                    session_id=session_id,
                    run_id=run_id,
                    window_index=window_index,
                    notes=candidate,
                )
                notes = candidate
                logger.info("回合收尾笔记已更新（窗口 W%s）", window_index)
            else:
                # 宁可留给下一轮一份旧笔记，也不能写进一份残缺的
                logger.warning("回合收尾笔记校验未通过，保留上一版：%s", problems)
            if turn_span is not None:
                with contextlib.suppress(Exception):
                    turn_span.update(
                        output={
                            "updated": candidate is not None and not problems,
                            "note": candidate.model_dump() if candidate else None,
                            "problems": problems,
                            "dropped_refs": dropped_refs,
                            "dropped_non_tool_steps": dropped_steps,
                        }
                    )

    # 回合结束：把终态写进 checkpoint（前端可能还在重连，需要知道"已经完了"）
    if checkpoint is not None:
        checkpoint.status = "finished"
        checkpoint.final_text = final_text
        checkpoint.steps = [step.dump() for step in steps]

    # 回传给调用方（sessions_router）：
    #   text           最终回答，落进 assistant 消息的 content.text
    #   steps          全部步骤，落进 content.steps（前端渲染时间线）
    #   rounds         这里存的实际是**步骤数**（历史命名，前端按这个语义在用）
    #   tool_call_ids  本次用到的 tool_calls 主键，供前端/排查按需拉详情
    return TurnResult(
        text=final_text,
        steps=[step.dump() for step in steps],
        rounds=len(steps),
        tool_call_ids=tool_call_ids,
        # 最终回答也要可寻址，sessions_router 拿这两个值给 assistant 消息盖坐标。
        # 正常收尾时上面已经为它占了号（digest 里 kind="answer" 那条），两边必须是同一个；
        # 撞到轮次上限、或压根没给出回答时没占过号，这里补一个。
        window_index=window_index,
        seq=answer_seq or (window_seq + 1),
    )
