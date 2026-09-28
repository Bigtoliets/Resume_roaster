"""会话辅助：只有一件小事 —— 给新会话取个名字。

**`build_history()` 已在 D53 删除。**它原来是"喂给模型的历史从哪来"的唯一来源
（每轮拼最近 8 条纯对话消息的全文），代价是每回合重复付费一大段 token。现在跨回合的
连续性由「注入最新一条任务笔记 + 按指针 recall 原文」承担，见 `app/agent/loop.py`
的 `load_notes()` / `save_notes()`。

剩下的是 **`make_title()`**：Agent 名 + 首条消息摘要，不额外调模型。

⚠️ **"对话太长"已经不在这里解决了**：D48 起改为**窗口换页 + 任务笔记**
（见 `app/agent/notes.py` 与 `app/agent/loop.py`）——上下文快满时整段切掉，
连续性由结构化笔记和存档指针承担。原来的滚动摘要 `summarize_if_needed()` /
`sessions.summary` 水位线机制已废弃删除：它是有损压缩，和"原文永久可回查"冲突。

一个贯穿全文件的原则：**这些辅助功能失败都不能影响对话本身**，
取不到就退回一个兜底标题，绝不往上抛异常。
"""

from __future__ import annotations


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
