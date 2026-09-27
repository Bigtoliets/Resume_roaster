"""回查存档（D48）：按指针取回被切掉的窗口里的原始记录。

换页之后模型只看得到笔记里的"要点"，看不到原文。当它需要精确内容
（比如简历里某一句原话、某条 JD 的准确措辞）时，用这个工具按指针回查——
存档是全量的、只读的，所以拿回来的永远是原文，不是模型记忆里的转述。

指针有两种：
    W2.M5  → 第 2 个窗口的第 5 条**消息**（查 messages 表）
    W2.T3  → 第 2 个窗口的第 3 次**工具调用**（查 tool_calls 表，返回完整结果）

入参收单个 `pointer` 或一次多个 `pointers`（模型实测更爱用后者）。
**这条工具是换页机制的必要条件**：Agent 没勾它就等于没有回查能力，
换页后原文彻底拿不回来（真实翻车：模型只能让用户重新上传简历）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.tools.base import ToolContext
from app.tools.registry import tool

#: 单次回查返回的正文上限：存档可能很大，避免一次把上下文又撑爆
MAX_RECALL_CHARS = 6000


@tool(
    impl_key="recall",
    title="回查存档",
    description=(
        "按指针取回原始记录。笔记里只有要点，需要精确原文时（例如某句简历原话、"
        "某条 JD 的准确措辞）调用它。指针形如 W2.M5（消息）或 W2.T3（工具调用）。"
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
                "description": "一次回查多个指针，例如 [\"W1.T2\", \"W1.T3\"]；与 pointer 二选一",
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
    pointers = [str(item).strip() for item in raw if str(item).strip()][:8]
    if not pointers:
        return {
            "ok": False,
            "summary": "缺少 pointer/pointers 参数，格式应为 W2.M5 或 W2.T3",
            "error": "missing_pointer",
        }

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for pointer in pointers:
        item, state = await _fetch(ctx, pointer)
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


async def _fetch(ctx: ToolContext, pointer: str) -> tuple[dict[str, Any] | None, str]:
    """按一个指针取回原始记录，返回 `(记录, 状态)`；状态是 ok / not_found / ambiguous。"""
    from app.agent.notes import POINTER_RE
    from app.db.models import Message, ToolCall

    match = POINTER_RE.match(pointer)
    if match is None:
        return None, "not_found"
    window_index, kind, seq = int(match.group(1)), match.group(2), int(match.group(3))

    # ⚠️ 必须按**回合**过滤，不是按会话。`W1.T2` 这种编号只在**本回合内**唯一：
    # 每个回合的窗口号都从 W1 重新开始，所以同一会话里第二回合的 `W1.T2`
    # 会和第一回合重名。实测踩过两次：
    #   1. 完全不限作用域 → 横跨所有会话，一次查出几十行；
    #   2. 只按 session_id 过滤 → 同一会话第二回合开始撞（MultipleResultsFound，
    #      模型连试两次全失败，整个诊断卡死）。
    # 只用 `.first()` 会更糟：它不会报错，而是**悄悄取到别的回合那一行**。
    if ctx.session_id is None:
        return None, "not_found"
    scope = (
        ToolCall.run_id == ctx.run_id if ctx.run_id is not None else ToolCall.session_id == ctx.session_id
    )

    if kind == "M":
        # 消息类：取那一条消息的正文（可能是用户提问或助手回答）
        msg_scope = (
            Message.run_id == ctx.run_id
            if ctx.run_id is not None
            else Message.session_id == ctx.session_id
        )
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
        text = str((row.content or {}).get("text") or "")[:MAX_RECALL_CHARS]
        return (
            {
                "pointer": pointer,
                "kind": "message",
                "label": f"{row.role} 消息 {len(text)} 字",
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
    payload = json.dumps(call.result or {}, ensure_ascii=False)[:MAX_RECALL_CHARS]
    return (
        {
            "pointer": pointer,
            "kind": "tool",
            "label": f"工具「{call.tool_name}」结果 {len(payload)} 字符",
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
