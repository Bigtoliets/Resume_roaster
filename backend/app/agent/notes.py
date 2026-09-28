"""任务笔记（D48）：换页时把当前窗口投影成结构化状态，作为跨窗口唯一的连续性来源。

════════════════════════════════════════════════════════════════════
为什么是"笔记"而不是"摘要"
════════════════════════════════════════════════════════════════════
摘要是对历史的**有损压缩**：压过就回不来了。笔记不同——它只记"任务现在处于什么状态"
（目标 / 进展 / 已定结论 / 下一步 / 关键引用），原始内容全部留在存档里，
需要原文时用指针 `W2.T3` 回查。所以笔记可以写得很短，信息也不会真的丢。

骨架固定、内容自由：字段就这五个，但里面写什么由模型决定——
换一个 Agent、换一组工具，笔记 schema 都不用改。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

#: 指针格式：`W<窗口号>.M<消息序号>` / `W<窗口号>.T<工具序号>`
POINTER_RE = re.compile(r"^W(\d+)\.([MT])(\d+)$")

#: 投影输入里单条记录的字符上限。超过就截断，但**必须留下显式标记**说明
#: "这是投影输入的摘录，不是工具输出被截断"（见 render_window_digest）。
RENDER_ENTRY_CHARS = 1200

#: `progress.stage` 每条的上限。它是"用哪个工具干了什么"的一行摘要，
#: 超过这个长度说明模型又在往里抄细节了 —— 直接截断，别让它把笔记撑成报告。
STEP_MAX_CHARS = 40



class Ref(BaseModel):
    """一条关键引用：只存指针 + 一句说明，不存原文。"""

    pointer: str          # 形如 W2.M5 / W2.T3
    summary: str = ""
    kind: str = "tool"    # message | tool


class Progress(BaseModel):
    """任务进展：**每完成一步追加一条**的列表（不是一段散文）。

    只有一个字段、且它是列表：一条 = 一个已完成的步骤，写清"用什么工具干了什么任务"。
    换页后模型扫一眼就知道走到哪了，不用从三百字大杂烩里刨状态。
    待确认的信息并入相关那一步的末尾（标「（待确认）」），不单开字段。
    """

    #: 已完成的步骤，一条一件事
    stage: list[str] = Field(default_factory=list)

    @field_validator("stage", mode="before")
    @classmethod
    def _coerce_steps(cls, value: Any) -> Any:
        """模型偶尔把列表写成一段文字（旧格式也是这样）。包成单元素列表，别判失败。"""
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value or []


class Notes(BaseModel):
    """任务工作集。字段固定，内容自由。

    刻意**没有** `decisions`：与 `progress.done` 职责重叠，实测模型会在两边各写一遍，
    笔记凭空膨胀一倍。结论就写在 `done` 的条目里（"读完简历，共 870 字"这种）。
    `plan` 也刻意只要**一条**——它是"下一步"，不是路线图。
    """

    goal: str = ""
    progress: Progress = Field(default_factory=Progress)
    plan: list[str] = Field(default_factory=list)
    refs: list[Ref] = Field(default_factory=list)

    @field_validator("progress", mode="before")
    @classmethod
    def _coerce_progress(cls, value: Any) -> Any:
        """兼容旧格式：`progress` 曾是一整段字符串。

        模型很可能按旧习惯继续回字符串；如果这里直接判失败，投影整体失败、
        换页发生不了，上下文会一路涨到顶（比结构不整齐严重得多）。
        降级成 `stage` 就够了 —— 信息一点没丢，只是没分条。
        """
        if isinstance(value, str):
            return {"stage": value}
        if value is None:
            return {}
        if isinstance(value, dict) and ("done" in value or "missing" in value):
            # 中间那版结构是 {stage(字符串), done, missing}。合并成步骤列表，别把已完成的事丢了。
            head = value.get("stage")
            steps = [head] if isinstance(head, str) and head.strip() else []
            steps.extend(value.get("done") or [])
            steps.extend(f"{item}（待确认）" for item in (value.get("missing") or []))
            return {"stage": steps}
        return value

    @field_validator("plan", mode="before")
    @classmethod
    def _coerce_plan(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value or []

PROJECTION_SYSTEM = """
【角色】任务状态投影器
【核心目标】将刚结束的完整窗口记录，压缩为一份**可独立续跑的最小状态笔记**。
窗口切换后模型只能看到这份笔记，看不到任何历史原文。
唯一标准：一个完全陌生的人，只看这份笔记，就能精准接着任务往下做，不跑偏、不重复、不遗漏。

【输入】
1. 上一版任务笔记（可能为空，首次投影时无历史状态）
2. 本窗口完整推理记录（含带指针的工具调用、思考过程、对话消息、本轮最终回答）

━━━ 输出格式（严格遵守，字段不可增减修改）━━━
{
  "goal": "这次任务要达成什么（一句话，不能为空）",
  "progress": {
    "stage": ["<工具名> <用它干了什么>，一条一个工具，一句话"]
  },
  "plan": ["下一步要做的那一件事（只有一条）"],
  "refs": [
    {"pointer": "W1.T3", "summary": "这条记录里是什么", "kind": "tool"}
  ]
}

━━━ 核心红线（触犯直接导致任务失败）━━━
1. 严禁编造进度：思考、计划、打算不算已完成事项，禁止把「准备做XX」写成「已完成XX」
2. progress.stage 默认**追加**：上一版已有的条目原样保留；被本窗口推翻的，保留并标注「（已推翻：原因）」，禁止直接删除。
   仅当满足下列**任一**条件时，才允许归档（合并或删掉）已完成条目：progress.stage 超过 20 条、或笔记整体已明显偏大。
   归档时必须把它们合并成一行摘要（如「已完成：读简历、检索 JD、技术面、HR 面，原文指针见 refs」），
   并且**这些条目对应的指针必须已经在 refs 里**——归档等于把细节完全托付给指针，指针没留就是真丢信息，禁止归档
3. 严禁 refs 指向 recall 工具：必须指向产生内容的原始工具调用；recall 只是取数手段，指向它会造成嵌套回查、内容冗余
4. 严禁误判结果截断：记录里出现「完整结果共N字」「此处仅摘录」是显示限制，原文完整存在存档；refs.summary 禁止写「截断」「不全」「无法获取」
5. 严禁使用非法指针：pointer 只能是 Wx.Tx / Wx.Mx 格式，或上一版笔记原样保留的编号；禁止填文件路径、URL、工具名、数据主键
6. 严禁原文抄入笔记：所有细节、完整数据留存在存档，笔记只存结论+指针

━━━ 字段级执行规则━━━
■ goal 字段
- 一句话明确任务核心目标，全程锚定不跑偏，不能为空
- 长度 ≤ 60 字，与用户原始任务方向保持一致

■ progress.stage 字段
- 只记录**已真实发生的工具调用**，一条对应一次带指针的工具调用；思考、纯判断、计划均不记录
- 每条必须以「调用工具 XXXX」开头，一句话说明产出结果，单条 ≤ 40 字（与代码 STEP_MAX_CHARS 一致）
- 只统计带 [Wx.Tx] 指针的工具条目，[—] 开头的思考条目不计入
- 上一版已有的条目原样保留，不得丢失；被推翻的条目保留并标注原因
- 不罗列细节数据，细节存入 refs 按需回查
- 待确认信息标注在对应条目末尾「（待确认）」，不单开字段

■ plan 字段
- 有且仅有 1 条，是本窗口最后一次思考里明确的下一步动作
- 直接取用模型原话精简，禁止自行规划、禁止罗列完整路线图
- 若没有明确下一步，留空数组

■ refs 字段
- 只保留后续大概率需要回查原文的条目，已完全消化的结论不留
- 三要素必填，缺一不可：
  - pointer：原始工具/消息的指针编号
  - summary：必须明确哪个工具、什么内容，禁止只写「工具结果」；可标注字数辅助判断召回成本
  - kind：tool（工具调用）/ message（对话消息）
- 只保留确实命中的指针，未命中、不存在的编号严禁写入
- 必须指向产生内容的原始工具调用，禁止指向 recall 的返回结果

■ refs 里的**对话条目**（kind="message"，与工具条目平权）
- 工具条目记「做了什么」，对话条目记「用户要什么、结论是什么」——两者都要有，不要只留工具
- 本窗口的用户消息（带 [Wx.Mx]）和本轮最终回答（带 [Wx.Mx]）都要各留一条
- 换话题或需求变更时，用户的**原话要点**尤其要留，并写明是哪一轮提出的
- 工具条目与对话条目共用同一个 refs 列表，按发生时间排列

━━━ 格式与体量约束━━━
1. 仅输出标准 JSON，不包裹代码块，JSON 前后不输出任何解释文字
2. 体量上限：goal ≤ 60 字；progress.stage 单条 ≤ 40 字；plan ≤ 1 条；
3. 语言与任务保持一致，使用中文

━━━ 参考示例━━━
输入：上一版笔记为空；窗口包含简历提取、JD检索、技术面诊断3次工具调用；最后思考为「接下来补HR视角核验」
输出：
{
  "goal": "诊断这份大数据开发实习生简历，给出致命弱点与改进建议",
  "progress": {
    "stage": [
      "调用工具 resume_pdf_extract 读到简历正文 3238 字",
      "调用工具 kb_search_perf 取到 3 条岗位要求",
      "调用工具 interview_tech_lead 产出 4 条技术质疑"
    ]
  },
  "plan": ["补 HR 视角的硬性门槛与量化产出"],
  "refs": [
    {"pointer": "W1.T2", "summary": "resume_pdf_extract 返回的简历正文全文，870 字", "kind": "tool"},
    {"pointer": "W1.T3", "summary": "kb_search_perf 返回的岗位JD与考点，命中12次", "kind": "tool"},
    {"pointer": "W1.T4", "summary": "interview_tech_lead 的技术面质疑原文", "kind": "tool"}
  ]
}
"""



def render_window_digest(entries: list[dict[str, Any]]) -> str:
    """把本窗口的"思考流水 + 观察摘要"渲染成投影的输入。

    注意：**只喂思考与观察摘要，不喂工具返回的完整 JSON** —— 那几万字符正是我们要甩掉的东西。
    """
    lines: list[str] = []
    for item in entries:
        pointer = item.get("pointer") or ""
        kind = item.get("kind") or ""
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        label = {"thought": "思考", "tool": "工具结果", "user": "用户", "answer": "助手"}.get(kind, kind)
        # 工具条目带上工具名。摘要文本本身常常看不出"这是谁returned的"
        # （实测：HR 面的结果被记成了技术面的），带上名字模型才能写对 refs.summary。
        tool_name = str(item.get("tool") or "").strip()
        if kind == "tool" and tool_name:
            label = f"工具 {tool_name} 结果"
        # `(第N轮)` 是给 `decisions[].round` 用的：不写进记录的话，模型根本无从知道
        # 某条结论出自第几轮，只能瞎猜。轮次为 0（如用户原话、换页后的续跑）就不标。
        round_no = int(item.get("round") or 0)
        round_tag = f"(第{round_no}轮) " if round_no else ""
        # 没有指针的条目（思考流水）用 `[—]` 占位，表示"这条不可回查"，
        # 免得模型把它当成一个能填进 refs 的编号。
        body = text[:RENDER_ENTRY_CHARS]
        if len(text) > RENDER_ENTRY_CHARS:
            # 这里被截断的是**投影输入**，不是工具输出 —— 不写清楚的话，
            # 投影会把"看不到结尾"理解成"工具结果被截断"，再写进笔记误导主模型
            # （实测：笔记里出现"截断发生在工具输出存储层"，然后模型不停重跑面试官工具）。
            body += f"…[此处仅为投影输入的摘录，原文共 {len(text)} 字，需要时按指针回查]"
        lines.append(f"[{pointer or '—'}] {round_tag}{label}：{body}")
    return "\n".join(lines) or "（本窗口没有可投影的内容）"


async def project_notes(
    *,
    llm: Any,
    previous: Notes | None,
    window_digest: str,
    latest_thought: str = "",
) -> Notes:
    """把上一版笔记 + 本窗口摘要，投影成新一版笔记。

    `previous` 为空是合法输入（第一次换页）—— 那就直接基于本窗口生成初始笔记。
    解析失败会由 `complete_json` 自动带错误重试一次。

    `latest_thought` 是模型在本窗口**最后一次 think 的原文**，单独喂进去是为了让
    `plan` 能直接取它、而不是让投影器自己编一个计划（用户明确要求 plan = 最新的 think）。
    """
    previous_text = (
        previous.model_dump_json(indent=2) if previous is not None else "（还没有笔记，这是第一次投影）"
    )
    thought_block = (
        f"【模型在本窗口的最后一次思考（plan 直接取它）】\n{latest_thought}\n\n"
        if latest_thought.strip()
        else "【模型在本窗口没有留下思考文本】\n\n"
    )
    messages = [
        {"role": "system", "content": PROJECTION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"【上一版笔记】\n{previous_text}\n\n"
                f"【刚结束的窗口记录】\n{window_digest}\n\n"
                f"{thought_block}"
                "请输出更新后的任务笔记 JSON。"
            ),
        },
    ]
    # 名字写清楚：trace 上这条 generation 要能一眼看出是"系统在做记忆投影"，
    # 而不是模型在思考（主循环的调用叫 llm-call，两者混在一起没法排查）。
    return await llm.complete_json(messages, Notes, name="notes-projection")


def normalize_notes(
    notes: Notes,
    previous: Notes | None,
    tool_names: set[str] | None = None,
    window_tool_names: set[str] | None = None,
) -> Notes:
    """把投影结果修成可用形态 —— **能修的自动修，别为小毛病放弃整个换页**。

    第一次真实运行就暴露了：模型会把文件路径当成 pointer 写进 refs，还会漏写 goal。
    如果这类问题都当"校验失败"，换页就永远发生不了，上下文继续涨到顶。
    所以：
      - 指针格式非法的 ref → 直接丢弃（refs 只是辅助，丢一条不影响任务连续性）
      - goal 为空 → 沿用上一版；上一版也没有才真的算失败
      - `progress` 整体为空 → 沿用上一版的 stage，没有就留个占位
      - `plan` 写了多条 → **只保留第一条**（"下一步"只有一件，多出来的属于路线图，
        正是这次要砍掉的东西；真要补，模型下一轮会自己重新规划）
      - `progress.stage` 里的条目**必须提到某个真实工具名**（传了 `tool_names` 时）——
        用户要的是"用什么工具干了什么事情"，`直接判断：…` 这类非工具步骤一律丢弃；
        每条还截到 `STEP_MAX_CHARS` 字，防止模型把细节抄进来
      - 传了 `window_tool_names` 时，**新增**条目还必须对应"本窗口真的跑过、并且留下指针的工具"。
        这条挡住的是实测最严重的一种编造：投影把模型的**思考**（"接下来问技术面"）
        记成了**已完成**（"interview_hr 判定硬门槛基本符合"），而那个工具根本没被调用；
        下游模型信了笔记，就去猜它的指针（W1.T6/T7/T8…），一个回合 11 次 recall、3 次报错。
    """
    notes.refs = [ref for ref in notes.refs if POINTER_RE.match(ref.pointer or "")]
    if not notes.goal.strip() and previous is not None:
        notes.goal = previous.goal
    if tool_names:
        kept = [step for step in notes.progress.stage if any(name in step[:40] for name in tool_names)]
        if not kept and previous is not None and previous.progress.stage:
            # 全被过滤掉时沿用上一版，避免"校验失败 → 不换页 → 上下文继续涨"这个更糟的结果
            kept = previous.progress.stage
        notes.progress.stage = kept
    if window_tool_names is not None:
        # 沿用判定**按工具名**，不能按整串精确匹配：投影经常把沿用条目重新措辞
        # （"读到简历正文 870 字（张小明，2027…" → "读到简历正文 870 字"），
        # 精确匹配会把它们全部误杀 —— 实测笔记里的步骤就是这么从 3 条缩到 1 条的。
        carried_tools: set[str] = set()
        if previous is not None:
            for step in previous.progress.stage:
                carried_tools.update(name for name in (tool_names or set()) if name in step[:40])
        notes.progress.stage = [
            step
            for step in notes.progress.stage
            # 上一版记过的工具照旧放行；**新**工具必须在本窗口真的跑过、且留下指针
            if any(name in step[:40] for name in (window_tool_names | carried_tools))
        ]
    notes.progress.stage = [
        step if len(step) <= STEP_MAX_CHARS else f"{step[:STEP_MAX_CHARS]}…"
        for step in notes.progress.stage
    ]
    if not notes.progress.stage:
        notes.progress.stage = (
            previous.progress.stage
            if previous is not None and previous.progress.stage
            else ["（投影未给出已完成步骤）"]
        )
    if len(notes.plan) > 1:
        notes.plan = notes.plan[:1]
    return notes


def verify_notes(notes: Notes, previous: Notes | None) -> list[str]:
    """致命问题的校验：返回问题列表（空 = 通过）。

    刻意**不做"让模型自检"**（问它"完整吗"大概率答完整，还多花一次调用），
    也**不把格式小毛病当失败**（见 normalize_notes）。
    这里只拦真正会丢信息的两种情况。
    """
    problems: list[str] = []
    if not notes.goal.strip():
        problems.append("goal 为空且上一版也没有可沿用的目标")

    # 上一版"已完成"的条目不允许凭空消失（可以是被推翻后改写，但不能一条不剩）——
    # 这条是防止模型换页后把做过的事忘光、又从头做一遍。
    if previous is not None and previous.progress.stage and not notes.progress.stage:
        problems.append(
            f"上一版 progress.stage 有 {len(previous.progress.stage)} 条，新版一条都没有——"
            "已完成的事不该凭空消失；如果确实被推翻，请在原条目后补「（已推翻：…）」"
        )
    return problems
