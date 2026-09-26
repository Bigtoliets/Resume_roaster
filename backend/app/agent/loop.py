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
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approvals import wait_for_approval
from app.agent.prompts import DEFAULT_AGENT_SYSTEM, render_variables
from app.agent.tool_docs import render_tools_text
from app.core.security import decrypt
from app.db.models import Agent, ModelConfig, Prompt, Skill, Tool, ToolCall
from app.llm.client import LLMClient, ToolCallRequest
from app.tools.registry import build_tool_schema, execute_tool, get_impl, impl_status

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

#: 文本协议里的 Action 标记：`Action: {...}` / `Action：{...}`
_ACTION_RE = re.compile(r"Action\s*[:：]\s*(\{)", re.S)


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
    """
    if not text:

        return None
    match = _ACTION_RE.search(text)
    if match is None:
        return None
    raw = _extract_json_object(text, match.start(1))
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


async def _invoke_parallel(call: Any, tool_id: int, llm: LLMClient) -> tuple[Any, dict[str, Any], str, int]:
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
            tool_db, tool=tool, arguments=call.arguments, fallback_llm=llm
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


async def run_react_turn(
    db: AsyncSession,
    *,
    session_id: int,
    run_id: int | None,
    agent: Agent,
    user_content: str,
    history: list[dict[str, str]],
    summary: str,
    attachments: list[dict[str, Any]],
    emit: EmitFn,
    approval_timeout_s: float = 180,
) -> TurnResult:
    """跑完一个完整的 ReAct 回合，返回最终回答与全部步骤。

    参数里的 `emit` 是 SSE 推送函数：每产生一条步骤/一个 token 就推给前端一次，
    前端据此实时显示"思考 → 行动 → 观察"的进度。数据库写入则集中在回合结束时
    （消息 + 步骤 JSON），中间只落 `tool_calls` 表便于展开查看完整参数与结果。
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

    # ── 2. 组装上下文：模型客户端 + system prompt + 历史 + 本轮输入 ────────
    llm = await build_agent_llm(db, agent)
    system_prompt = await build_system_prompt(db, agent)
    if skipped:
        logger.info(
            "Agent %s 有 %s 个工具被白名单挡下（未实现/未指定）：%s",
            agent.name,
            len(skipped),
            "、".join(tool.name for tool in skipped),
        )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append({"role": "system", "content": f"【会话摘要】\n{summary}"})
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})

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
    #: 本轮新增消息的起点（只影响 Langfuse 上报，见 client._report_generation）
    new_from = 0

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
                    # 取 answer 当最终回答；模型偶尔忘写 answer，就退回整段文本。
                    answer = str(tool_args.get("answer") or "").strip()
                    final_text = answer or result.content
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
            final_text = result.content or "（模型没有返回内容）"
            break

        # 模型可能先嘀咕几句再决定调工具。这段嘀咕作为一条"思考"落库 + 推送。
        # `[:600]` 是防止它长篇大论把消息表撑爆（完整内容不需要留）。
        if result.content:
            thought = Step(type="thought", text=result.content[:600])
            steps.append(thought)
            await emit("step", thought.dump())

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
                        _invoke_parallel(call, tools_by_name[call.name].id, llm)
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
                db.add(record)
                await db.commit()
                await db.refresh(record)

                # 三种取结果的方式，按优先级排列：
                #   1. 并发预取已经跑完 → 直接取结果
                if call.id in parallel_direct:
                    observation, status, elapsed_ms = parallel_direct[call.id]
                    record.elapsed_ms = elapsed_ms
                #   2. 需要人工确认 → 推审批请求并挂起等待（超时按拒绝处理）
                elif tool.requires_approval:
                    record.status = "pending_approval"
                    await db.commit()
                    await emit(
                        "approval_required",
                        {
                            "tool_call_id": record.id,
                            "tool": tool.name,
                            "tool_display": display,
                            "arguments": call.arguments,
                            "timeout_s": int(approval_timeout_s),
                        },
                    )
                    approved = await wait_for_approval(record.id, approval_timeout_s)
                    record.approved = approved
                    await db.commit()
                    if not approved:
                        # 拒绝时同样回灌一条观察结果，措辞是写给**模型**看的：
                        # 引导它换个办法，而不是让整轮就此卡住
                        observation = {
                            "ok": False,
                            "summary": "用户拒绝执行该工具，请换个方式或直接说明无法完成",
                        }
                        status = "rejected"
                    else:
                        # 批准后正常执行（串行，用主会话）
                        started = time.perf_counter()
                        observation = await execute_tool(
                            db, tool=tool, arguments=call.arguments, fallback_llm=llm
                        )
                        status = "ok" if observation.get("ok", True) else "error"
                        record.elapsed_ms = int((time.perf_counter() - started) * 1000)
                #   3. 直接执行（串行）。fallback_llm 让"没单独配模型的工具"复用 Agent 的模型
                else:
                    started = time.perf_counter()
                    observation = await execute_tool(
                        db, tool=tool, arguments=call.arguments, fallback_llm=llm
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

            # 回灌给模型：这是 ReAct 的"观察"环节，也是下一轮模型能看到的**新信息**。
            # `[:12000]` 防止一次大结果把上下文撑爆。
            payload_json = json.dumps(observation, ensure_ascii=False)[:12000]
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
        new_from = round_baseline
    else:
        # for...else：只有循环**自然跑完**（没有 break）才执行，即 max_rounds 轮都没得出结论。
        # 给一句明确交代，而不是返回空让用户以为出错了。
        final_text = "（已达到最大轮次，先给出当前结论）"
        await emit("token", {"round": max_rounds, "delta": final_text})

    # 最后一道兜底：万一上面所有分支都没能给出文本（例如工具全失败又没有收尾语）
    if not final_text:
        final_text = "（模型没有给出最终回答）"
        await emit("token", {"round": 0, "delta": final_text})

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
    )
