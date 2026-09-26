"""会话辅助：历史拼装 + 滚动摘要 + 自动命名（D31 起不再有路由与场景）。

这个文件解决三个问题，都是"多轮对话"绕不开的：

  1. **喂给模型的历史从哪来** —— `build_history()`：只取最近的纯对话消息，
     工具返回的大 JSON、报告原文这些都不进上下文。
  2. **对话太长怎么办** —— `summarize_if_needed()`：超过阈值就把老消息压成一段摘要，
     摘要存进 `sessions.summary`，并记录"压到哪条消息为止"（水位线），避免重复压缩。
  3. **会话列表显示什么名字** —— `make_title()`：Agent 名 + 首条消息摘要，不额外调模型。

一个贯穿全文件的原则：**这些辅助功能失败都不能影响对话本身**。
摘要调模型失败就保留旧摘要、历史取不到就退化成空列表，绝不往上抛异常。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, Session

logger = logging.getLogger(__name__)

#: 能进对话历史的**消息类型**白名单。
#: messages 表里还有 file（附件）、report（旧版结构化报告）等类型，
#: 它们要么是元数据、要么体积巨大，塞进上下文只会烧 token，所以这里只放行纯文本。
TEXT_TYPES = {"text"}


async def build_history(
    db: AsyncSession, session_id: int, window: int, *, before_id: int | None = None
) -> list[dict[str, Any]]:
    """取最近 `window` 条纯对话消息，转成 OpenAI 的 messages 格式。

    参数：
        db          —— 数据库会话
        session_id  —— 取哪个会话的历史
        window      —— 最多取多少条（按消息条数，不是轮数）
        before_id   —— 只取 id 小于它的消息；用于"这轮消息已经落库了，但拼上下文时
                       不该把自己也算进历史"的场景（sessions_router 会传最后一条用户消息的 id）

    返回形如 `[{"role": "user"|"assistant", "content": "..."}]`，可直接拼进 messages。
    只返回纯文本消息：附件、报告、工具调用明细都不进上下文。
    """
    # 只查这个会话的、类型在 TEXT_TYPES 白名单里的消息
    stmt = select(Message).where(
        Message.session_id == session_id, Message.type.in_(TEXT_TYPES)
    )
    # 可选的水位线：只看这个 id 之前的消息（不含自身）
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    # 按 id 倒序取最近的 window 条……（id 自增，等价于时间序）
    # max(1, window) 是防调用方传 0 或负数导致 LIMIT 非法
    stmt = stmt.order_by(Message.id.desc()).limit(max(1, window))
    # 执行并取回 ORM 行
    rows = list((await db.execute(stmt)).scalars().all())
    # 倒序是为了"取最近的 N 条"，取回来必须翻正，否则模型会看到倒着播的对话
    rows.reverse()
    history: list[dict[str, Any]] = []
    for row in rows:
        # content 是一个 JSON 列；不同类型结构不同，纯文本消息里只有 text 字段
        content = row.content or {}
        # 取出正文；取不到就空串（下面会跳过）
        text = str(content.get("text") or "")
        # 跳过空消息：模型收到空的 user/assistant 消息容易答非所问
        if not text.strip():
            continue
        # 归一化成两种角色：数据库里可能还有 system 等其他角色，
        # 这里只当作 assistant 处理（历史里的 system 提示词不属于对话记忆）
        history.append({"role": "user" if row.role == "user" else "assistant", "content": text})
    return history


def make_title(user_message: str, agent_name: str = "") -> str:
    """给会话起个名字：`Agent 名 · 首条消息摘要`。

    刻意不调模型——命名是高频操作，为它多打一次 LLM 既不划算也没必要，
    而且要保证**确定性**（同样的输入永远得到同样的标题，方便测试与排查）。
    """
    # 把所有空白（含换行）压成单空格，再截前 16 个字符，避免标题里出现换行、撑破列表
    snippet = " ".join((user_message or "").split())[:16]
    # 两个都有就拼起来；只有一个就用那个；都没有才退回"新会话"
    if agent_name and snippet:
        return f"{agent_name} · {snippet}"
    return snippet or (agent_name or "新会话")


async def summarize_if_needed(
    db: AsyncSession, session: Session, runtime: Any | None = None
) -> str | None:
    """对话变长时做**滚动摘要**：把老消息压成一段话，供后续轮次使用。

    为什么需要它：LLM 无状态，历史每轮都要重发；对话越长越贵，
    而且迟早会撞上上下文上限。这里把"已经聊过的内容"压成摘要，
    原始消息仍留在库里（只是不再进上下文）。

    关键设计：**水位线**（`sessions.summary_upto_message_id`）。
    只压缩"id 大于水位线"的消息，压完把水位线推到本轮最后一条，
    这样下次不会把刚压过的内容再压一遍。

    失败自动降级：调模型失败、返回空，都只是"这次不压"，保留旧摘要，
    **绝不影响当前对话**。

    注意：`runtime` 参数是旧架构（场景运行时）的遗留，现在没人用，保留仅为兼容调用签名。
    """
    # 触发阈值：未摘要消息累计到这个条数才压缩。
    # 名字里的 "scene" 是旧概念残留；值 8 目前硬编码，调大会更省（压缩次数少）但也更容易撞上限。
    scene_trigger = 8

    # 取"水位线之后"的所有纯文本消息，按时间正序（压缩要按发生顺序读）
    stmt = (
        select(Message)
        .where(
            Message.session_id == session.id,
            Message.type.in_(TEXT_TYPES),
            # 只取水位线之后的；水位线为 0 表示"从没压过"
            Message.id > (session.summary_upto_message_id or 0),
        )
        .order_by(Message.id.asc())
    )
    pending = list((await db.execute(stmt)).scalars().all())
    # 没到阈值就什么都不做（返回 None 表示"这次没产生新摘要"）
    if len(pending) < scene_trigger:
        return None

    # 把待压缩的消息拼成"用户：… / 助手：…"的纯文本，交给模型
    lines: list[str] = []
    for row in pending:
        text = str((row.content or {}).get("text") or "").strip()
        if not text:
            continue  # 跳过空消息，避免摘要里出现"用户：（空）"
        # 每条最多带 800 字：压缩输入也要控制体量，否则摘要本身又要花一大笔 token
        speaker = "用户" if row.role == "user" else "助手"
        lines.append(f"{speaker}：{text[:800]}")

    try:
        from app.core.config import settings
        from app.llm.client import LLMClient

        # 压缩用的提示词：要求保留"目标 / 已给的结论 / 未解决的问题"，丢掉寒暄与重复
        messages = [
            {
                "role": "system",
                "content": (
                    "你是会话压缩器。把对话压缩成一段供后续轮次使用的摘要："
                    "保留用户目标、已给出的关键结论、尚未解决的问题；丢弃寒暄与重复内容。"
                    "用第三人称、条目化，400 字以内。只输出摘要正文。"
                ),
            },
            {
                "role": "user",
                "content": (
                    # 把旧摘要也一起给它 —— 这是"增量压缩"，不是从头重写
                    f"已有摘要（可能为空）：\n{session.summary or '（无）'}\n\n新增对话：\n"
                    + "\n".join(lines)
                ),
            },
        ]
        # 注意：压缩**固定用 .env 的默认模型**，不跟随 Agent 的模型配置。
        # 理由：摘要属于基础设施，换 Agent 不该导致摘要风格跳变；而且这里拿不到 Agent 上下文。
        llm = LLMClient(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            model=settings.default_chat_model,
        )
        new_summary = (await llm.complete(messages)).strip()
    except Exception as exc:  # noqa: BLE001
        # 降级：保留旧摘要，本轮不做压缩。对话继续，只是上下文暂时变长一些
        logger.warning("会话摘要失败，保留旧摘要：%s", exc)
        return None

    # 模型返回空也当失败处理，避免把摘要覆盖成空串（那等于丢掉全部记忆）
    if not new_summary:
        return None
    # 写回：摘要正文（截断 4000 字防爆）+ 水位线推到本轮最后一条消息
    session.summary = new_summary[:4000]
    session.summary_upto_message_id = pending[-1].id
    await db.commit()
    logger.info("会话 #%s 摘要已更新（覆盖到消息 #%s）", session.id, session.summary_upto_message_id)
    return new_summary


def report_digest(report: dict[str, Any]) -> str:
    """把结构化报告压成一段纯文本，供"追问"时当历史使用。

    背景：报告本体很长，不适合整段进上下文；但用户追问"那第 2 条怎么改"时，
    模型必须先知道报告说了什么。所以这里只抽五个要点：
    目标岗位 / 一句话判决 / 致命弱点 / 改写建议（一条示例）/ 路线图前四周。

    注意：这是**旧版结构化报告**（report 消息类型）的摘要器。
    现在 ReAct 链路产出的是纯文本回答，走不到这里；保留是为了兼容历史会话。
    """
    lines = [
        "【诊断报告摘要】",
        # meta.target_role：报告生成时记录的目标岗位
        f"目标岗位：{(report.get('meta') or {}).get('target_role') or '未指定'}",
        # headline：报告里那句"一句话判决"
        f"一句话判决：{report.get('headline') or '—'}",
    ]
    # 致命弱点：最重要的部分，逐条列出（带 category 与严重度，便于模型引用）
    weaknesses = report.get("fatal_weaknesses") or []
    if weaknesses:
        lines.append("致命弱点：")
        for index, item in enumerate(weaknesses, start=1):
            lines.append(
                f"{index}. [{item.get('category')} · 严重度 {item.get('severity')}] {item.get('claim')}"
            )
    # 改写建议：只带一条示例（说明"改写长什么样"），不把全部塞进上下文
    rewrites = report.get("rewrites") or []
    if rewrites:
        lines.append(f"改写建议共 {len(rewrites)} 条，例如：{rewrites[0].get('rewritten', '')[:120]}")
    # 路线图：只取前 4 周，避免摘要本身变成另一份长文
    roadmap = report.get("roadmap") or []
    if roadmap:
        lines.append(
            "12 周路线图要点："
            + "；".join(f"W{item.get('week')} {item.get('goal')}" for item in roadmap[:4])
        )
    # 总长兜底 1500 字：这段是要进对话历史的，必须可控
    return "\n".join(lines)[:1500]


def dump_json(value: Any) -> str:
    """把任意值序列化成 JSON 文本（`ensure_ascii=False` 保证中文不被转成 \\uXXXX）。"""
    return json.dumps(value, ensure_ascii=False)
