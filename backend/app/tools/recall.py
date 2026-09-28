"""回查存档（D48 / D50~D58）：按指针取回被切掉的窗口里的原始记录。

换页之后模型只看得到笔记里的"要点"，看不到原文。当它需要精确内容
（比如简历里某一句原话、某条 JD 的准确措辞）时，用这个工具按指针回查——
存档是全量的、只读的，所以拿回来的永远是原文，不是模型记忆里的转述。

指针有两种：
    W2.M5  → 第 2 个窗口的第 5 条**消息**（查 messages 表）
    W2.T3  → 第 2 个窗口的第 3 次**工具调用**（查 tool_calls 表，返回完整结果）

入参收单个 `pointer` 或一次多个 `pointers`（模型实测更爱用后者）。
**这条工具是换页机制的必要条件**：Agent 没勾它就等于没有回查能力，
换页后原文彻底拿不回来（真实翻车：模型只能让用户重新上传简历）。

改造要点（D50/D55/D57/D58）：
  1. **作用域是会话，不再是回合** —— 窗口号改成会话内跨回合累计，所以 `W1.T2`
     在整个会话里唯一，不需要再用 run_id 区分"第几个回合"。
  2. **条数上限 8 → 32**（是上限，不是定额）。
  3. **点名的每条都给，一条不丢**；每条给多深由总预算摊（`_per_item_chars`）。
     曾考虑过"按总量决定给哪几条"，否决——那会静默丢条目。
  4. **每条都报原文真实总字数**。改造前 label 用的是截断后的长度，等于告诉模型
     "就这么多"，这是笔记里出现"截断/不全"、进而反复回查的根因（见 D49）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.tools.base import ToolContext
from app.tools.registry import tool

#: 单次能点名多少个指针（D55）。是上限不是定额 —— 只在"合法批量被卡住"时起作用。
MAX_POINTERS = 32

#: 调用方没给预算时的保守兜底。正常路径由主循环按 D58 算好传进来
#: （`context_window × 阈值 × 50%`，单位字符）。
#:
#: 这里**刻意没有"单条硬上限"**：单条给多深完全由预算摊出来（D57）。改造前那个固定的
#: `MAX_RECALL_CHARS = 6000` 会把 D58 的百分比预算悄悄压掉 —— 预算算出 4 万字也只会给
#: 6000，"只要一条就吃满预算、尽量给全文"根本不会发生。
DEFAULT_RECALL_BUDGET_CHARS = 12000

#: 每条至少给这么多字：摊到极限也不能只剩一句摘要，否则"不丢条"就名存实亡。
MIN_ITEM_CHARS = 400


def budget_chars(ctx: ToolContext) -> int:
    """本次回查可用的字符预算。主循环没给时退回兜底值。"""
    return int(ctx.recall_budget_chars or DEFAULT_RECALL_BUDGET_CHARS)


def _per_item_chars(budget: int, count: int) -> int:
    """D57：点名的每条都给，深度按总预算摊 —— 不是"按总量决定给哪几条"。

    条数越多每条越浅，但**一条都不会被丢掉**；每条都会报出原文真实总字数，
    让模型自己判断"这条只拿到 400 字够不够，不够就单独再要它一次"。
    """
    if count <= 0:
        return budget
    return max(MIN_ITEM_CHARS, budget // count)


def _excerpt(text: str, limit: int) -> tuple[str, str]:
    """返回 `(给模型的正文, 一句话说明)`。**截断可以，静默截断不行**（D57）。"""
    if len(text) <= limit:
        return text, f"共 {len(text)} 字"
    return text[:limit], f"仅取前 {limit} 字，原文共 {len(text)} 字，需要更多可单独再查它一次"


@tool(
    impl_key="recall",
    title="回查存档",
    description=(
        "按指针取回原始记录。笔记里只有要点，需要精确原文时（例如某句简历原话、"
        "某条 JD 的准确措辞）调用它。指针形如 W2.M5（消息）或 W2.T3（工具调用）。"
        "一次可以点名多条（最多 32 条）；点名的条目都会返回，但条数越多每条给得越短，"
        "每条都会标出原文总字数，不够就单独再查它一次。"
        "只返回该记录的结果原文，不回灌调用参数。"
    ),
    params_schema={
        "type": "object",
        "properties": {
            "pointer": {
                "type": "string",
                "description": "单个存档指针，例如 W2.M5（消息）或 W2.T3（工具调用）",
            },
            "pointers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "一次回查多个指针（最多 32 条），例如 [\"W1.T2\", \"W1.T3\"]；"
                    "与 pointer 二选一；只点名当前步骤真正需要的，不要批量捞历史"
                ),
            }
        },
    },
)
async def run(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    args = input.get("args") or {}

    # 没有会话上下文时（工具测试场）指针无从定位：编号只在会话内唯一，
    # 这里直接说清楚，而不是返回一个让人误以为"指针写错了"的 not_found。
    if ctx.session_id is None:
        return {
            "ok": False,
            "summary": "当前不在会话上下文里，无法按指针回查存档（测试场不支持这条工具）",
            "error": "no_session_context",
        }

    # 实测发现模型天然会一次要多个指针（`{"pointers":["W1.T2","W1.T3"]}`）。
    # 只认单个 `pointer` 的话，它要么被拒、要么得跑两轮 —— 这里两种入参都收，
    # 并且顺手把逗号/空格分隔的字符串也拆开（模型偶尔会写成 "W1.T2, W1.T3"）。
    raw = args.get("pointers") or args.get("pointer") or []
    if isinstance(raw, str):
        raw = [item for item in raw.replace(",", " ").split() if item]
    pointers = [str(item).strip() for item in raw if str(item).strip()][:MAX_POINTERS]
    if not pointers:
        return {
            "ok": False,
            "summary": "缺少 pointer/pointers 参数，格式应为 W2.M5 或 W2.T3",
            "error": "missing_pointer",
        }

    # D57：点名的每条都给，深度按条数摊。预算由主循环按 D58 算好放进 ctx。
    per_item = _per_item_chars(budget_chars(ctx), len(pointers))

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for pointer in pointers:
        item, state = await _fetch(ctx, pointer, per_item)
        if state == "ambiguous":
            ambiguous.append(pointer)
        elif item is None:
            missing.append(pointer)
        else:
            items.append(item)

    if not items:
        if ambiguous:
            return {
                "ok": False,
                "summary": (
                    f"存档里 {'、'.join(ambiguous)} 对应多条记录，无法确定是哪一条。"
                    "这通常意味着指针跨了回合——每次换页后的窗口号只在**本回合**内唯一。"
                    "请只引用当前任务笔记里给出的指针。"
                ),
                "error": "ambiguous_pointer",
            }
        return {
            "ok": False,
            "summary": f"存档里找不到这些指针：{'、'.join(missing)}",
            "error": "not_found",
        }
    head = "、".join(f"{item['pointer']}（{item['label']}）" for item in items)
    tail_parts = []
    if missing:
        tail_parts.append(f"未命中：{'、'.join(missing)}")
    if ambiguous:
        tail_parts.append(f"指向多条记录：{'、'.join(ambiguous)}")
    tail = f"；{'；'.join(tail_parts)}" if tail_parts else ""
    return {
        "ok": True,
        "summary": f"回查命中 {len(items)} 条：{head}{tail}",
        # 单条只截一次总长：批量回查时尤其容易把上下文又撑爆
        "data": {"items": items, "missing": missing, "ambiguous": ambiguous},
    }


async def _fetch(
    ctx: ToolContext, pointer: str, per_item: int
) -> tuple[dict[str, Any] | None, str]:
    """按一个指针取回原始记录，返回 `(记录, 状态)`；状态是 ok / not_found / ambiguous。"""
    from app.agent.notes import POINTER_RE
    from app.db.models import Message, ToolCall

    match = POINTER_RE.match(pointer)
    if match is None:
        return None, "not_found"
    window_index, kind, seq = int(match.group(1)), match.group(2), int(match.group(3))

    # ⚠️ 作用域是**会话**（D50）。改造前这里必须按回合过滤，因为那时每个回合的
    # 窗口号都从 W1 重新开始，同一会话第二回合的 `W1.T2` 会和第一回合重名（实测
    # 报 MultipleResultsFound，模型连试两次全失败、整个诊断卡死）。
    # D50 把窗口号改成会话内跨回合累计之后，`(window_index, seq)` 在会话里唯一，
    # 按会话查就够，run_id 不再参与定位。
    #
    # 唯一残留的撞号来源是**改造前的老数据**（同会话里有多行 W1）。那时这里会返回
    # ambiguous 而不是取错行（下面显式判定 `len(rows) > 1`，刻意不用 `.first()` ——
    # 它不报错，而是悄悄取到别的回合那一行）。
    if ctx.session_id is None:
        return None, "not_found"
    scope = ToolCall.session_id == ctx.session_id

    if kind == "M":
        # 消息类：取那一条消息的正文（可能是用户提问或助手回答）
        msg_scope = Message.session_id == ctx.session_id
        rows = list(
            (
                await ctx.db.execute(
                    select(Message).where(
                        msg_scope,
                        Message.window_index == window_index,
                        Message.seq == seq,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(rows) > 1:
            return None, "ambiguous"
        if not rows:
            return None, "not_found"
        row = rows[0]
        text, note = _excerpt(str((row.content or {}).get("text") or ""), per_item)
        return (
            {
                "pointer": pointer,
                "kind": "message",
                "label": f"{row.role} 消息（{note}）",
                "role": row.role,
                "text": text,
            },
            "ok",
        )

    # 工具类：取那次调用的参数与完整结果
    calls = list(
        (
            await ctx.db.execute(
                select(ToolCall).where(
                    scope,
                    ToolCall.window_index == window_index,
                    ToolCall.seq == seq,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(calls) > 1:
        return None, "ambiguous"
    if not calls:
        return None, "not_found"
    call = calls[0]
    payload, note = _excerpt(json.dumps(call.result or {}, ensure_ascii=False), per_item)
    return (
        {
            "pointer": pointer,
            "kind": "tool",
            "label": f"工具「{call.tool_name}」结果（{note}）",
            "tool": call.tool_name,
            # ① 只给结果，**不回灌入参**：LLM 类工具（面试官/终裁）的入参就是简历 + JD 全文
            #    （实测一条 interview_tech_lead 的 arguments 有 4422 字，而结果只有 1563 字），
            #    回灌等于把简历和 JD 又塞回上下文一遍 —— 那些原文模型可以按各自的指针单独回查。
            # ② `result` 放在第一个：整个 observation 回主循环后还会被截到 12000 字符，
            #    排在最后的字段最先被砍，结果原文绝不能被砍。
            "result": payload,
            "summary": call.summary,
        },
        "ok",
    )
