"""会话辅助：历史拼装 + 自动命名（D31 起不再有路由与场景）。

这个文件只剩两件事，都是"多轮对话"绕不开的最小集：

  1. **喂给模型的历史从哪来** —— `build_history()`：只取最近的纯对话消息，
     工具返回的大 JSON、报告原文这些都不进上下文。
  2. **会话列表显示什么名字** —— `make_title()`：Agent 名 + 首条消息摘要，不额外调模型。

⚠️ **"对话太长"已经不在这里解决了**：D48 起改为**窗口换页 + 任务笔记**
（见 `app/agent/notes.py` 与 `app/agent/loop.py`）——上下文快满时整段切掉，
连续性由结构化笔记和存档指针承担。原来的滚动摘要 `summarize_if_needed()` /
`sessions.summary` 水位线机制已废弃删除：它是有损压缩，和"原文永久可回查"冲突。

一个贯穿全文件的原则：**这些辅助功能失败都不能影响对话本身**，
历史取不到就退化成空列表，绝不往上抛异常。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message

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
