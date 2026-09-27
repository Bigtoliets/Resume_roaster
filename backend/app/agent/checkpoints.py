"""进程内 checkpoint：让一个正在跑的 ReAct 回合可以「暂停 → 等确认 → 继续」。

════════════════════════════════════════════════════════════════════
为什么需要它
════════════════════════════════════════════════════════════════════
工具配了 `requires_approval` 时，回合要在执行前停下来等用户点确认。
最朴素的做法是让回合协程直接 `await` 一个事件——但那个协程是跑在 HTTP 请求里的，
**用户一刷新页面，连接断开，协程被取消，整个回合连同上下文一起蒸发**。

所以把状态显式搬进这里的 `TurnCheckpoint`：

    HTTP 请求 → 起一个后台任务跑回合 → 状态登记在这里
                      ↓
    用户刷新页面 → 查 checkpoint → 时间线与待确认卡片原样重建 → 重连 SSE
                      ↓
    用户点确认 → 这里唤醒后台任务 → 从暂停那行继续往下跑

**边界（说清楚免得误解）**：这是**进程内存**。后端重启，所有 checkpoint 消失，
等待中的回合就废了（DB 里那条 tool_call 会停在 pending_approval，需要人工收尾）。
要用多 worker 也会失效（请求落到别的进程就找不到状态）。单进程本地部署够用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 事件缓冲上限：SSE 断开期间事件继续累积，超过这个数就丢最老的，
#: 防止"没人看但回合还在跑"的场景把内存涨爆。
MAX_BUFFERED_EVENTS = 500

#: 孤儿清理阈值：等待确认超过这么久、用户一直没回来的回合会被清掉
ORPHAN_AFTER_S = 30 * 60


@dataclass
class PendingApproval:
    """当前正在等用户点确认的**那一次**工具调用。"""

    #: tool_calls 表主键。前端点按钮时回传它，用来定位。
    tool_call_id: int
    #: 工具内部名（tools 表的 name）。"本回合记住"就是按它记的。
    tool_name: str
    #: 卡片标题（中文显示名）
    tool_display: str
    #: 卡片副标题（工具说明一句话，让用户知道这工具是干嘛的）
    tool_hint: str
    #: 模型这次给的参数，卡片里展开看的就是它
    arguments: dict[str, Any]
    #: 创建时间（time.monotonic，用来算"剩余 4:12"）
    created_at: float
    #: 本次等待的上限（秒），来自 Agent 配置
    timeout_s: int
    #: 唤醒通道：后台任务在这上面 await，审批接口 set 它
    event: asyncio.Event = field(default_factory=asyncio.Event)
    #: 用户决定："" 未决定 / approved / cancelled / timeout
    decision: str = ""
    #: 用户是否勾了"本回合不再问我"
    remember: bool = False

    def remaining_s(self) -> int:
        """剩余秒数（给前端倒计时用）。"""
        elapsed = time.monotonic() - self.created_at
        return max(int(self.timeout_s - elapsed), 0)

    def dump(self) -> dict[str, Any]:
        """转成可 JSON 化的 dict（SSE 与 checkpoint 接口都用它）。"""
        return {
            "tool_call_id": self.tool_call_id,
            "tool": self.tool_name,
            "tool_display": self.tool_display,
            "tool_hint": self.tool_hint,
            "arguments": self.arguments,
            "remaining_s": self.remaining_s(),
            "timeout_s": self.timeout_s,
            "decision": self.decision,
            "remember": self.remember,
        }


@dataclass
class TurnCheckpoint:
    """一个正在跑的 ReAct 回合的进程内快照。"""

    run_id: int
    session_id: int
    agent_id: int
    agent_name: str

    # ── 回合作业面：唤醒后要接着用的东西 ──────────────────────────────
    #: 当前完整上下文（system + 历史 + 本轮输入 + 已回灌的观察结果）。
    #: 这是"续跑"的本质：不存它就只能从头重跑，前面的工具会白调一遍。
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: 当前跑到第几轮
    round_index: int = 0
    #: 已产生的步骤（前端按它重建时间线）
    steps: list[dict[str, Any]] = field(default_factory=list)
    #: 已落库的工具调用 id
    tool_call_ids: list[int] = field(default_factory=list)

    # ── 本回合的"记住"（用户确认过 / 取消过的工具）────────────────────
    #: 确认过的工具名：不再问，直接执行
    approved_tools: set[str] = field(default_factory=set)
    #: 取消过的工具名：不再问，直接回灌拒绝
    cancelled_tools: set[str] = field(default_factory=set)

    # ── 运行控制 ────────────────────────────────────────────────────
    #: 正在等确认的那一个；None 表示没在等
    pending: PendingApproval | None = None
    #: 后台跑这个回合的协程
    task: asyncio.Task[Any] | None = None
    #: 事件缓冲：SSE 断开期间照样往里写，重连后补发
    events: list[dict[str, Any]] = field(default_factory=list)
    #: running | waiting_approval | finished | failed
    status: str = "running"
    created_at: float = field(default_factory=time.monotonic)
    #: 最终回答（回合结束后前端可以直接取，不用等落库）
    final_text: str = ""

    def push_event(self, event: str, data: dict[str, Any]) -> None:
        """把一条 SSE 事件写进缓冲（超出上限就丢最老的）。"""
        self.events.append({"event": event, "data": data})
        if len(self.events) > MAX_BUFFERED_EVENTS:
            del self.events[: len(self.events) - MAX_BUFFERED_EVENTS]

    def dump(self) -> dict[str, Any]:
        """给 `GET /runs/{id}/checkpoint` 的响应体。"""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "round_index": self.round_index,
            "steps": self.steps,
            "pending": self.pending.dump() if self.pending else None,
            "approved_tools": sorted(self.approved_tools),
            "cancelled_tools": sorted(self.cancelled_tools),
            "final_text": self.final_text,
        }


#: run_id → 回合快照
_turns: dict[int, TurnCheckpoint] = {}
#: tool_call_id → 待确认项。
#: **独立于 checkpoint**：回合可能没有 checkpoint（没启用后台任务时），
#: 但只要在等确认，审批接口就必须能按 tool_call_id 找到它并唤醒。
_pending: dict[int, PendingApproval] = {}


def register(checkpoint: TurnCheckpoint) -> None:
    """回合开始时登记。"""
    _turns[checkpoint.run_id] = checkpoint


def get(run_id: int) -> TurnCheckpoint | None:
    return _turns.get(run_id)


def find_by_tool_call(tool_call_id: int) -> TurnCheckpoint | None:
    """按 tool_call_id 找等待中的条目所属的回合（没有 checkpoint 时返回 None）。"""
    for checkpoint in _turns.values():
        if checkpoint.pending is not None and checkpoint.pending.tool_call_id == tool_call_id:
            return checkpoint
    return None


def register_pending(checkpoint: TurnCheckpoint | None, pending: PendingApproval) -> None:
    """登记"正在等确认"的那一次调用，并把回合置为等待态。"""
    _pending[pending.tool_call_id] = pending
    if checkpoint is not None:
        checkpoint.pending = pending
        checkpoint.status = "waiting_approval"


async def wait_for_decision(pending: PendingApproval) -> str:
    """后台任务在这里挂起，直到用户点了确认/取消，或超时。

    返回三态字符串——**超时必须和"用户取消"区分开**：
    前者适合让模型换个不需要确认的做法继续，后者应当停止这个方向。
    """
    try:
        await asyncio.wait_for(pending.event.wait(), timeout=pending.timeout_s)
    except TimeoutError:
        pending.decision = "timeout"
        logger.info("工具调用 #%s 等待确认超时", pending.tool_call_id)
    return pending.decision or "cancelled"


def resolve_pending(tool_call_id: int, decision: str, *, remember: bool = False) -> bool:
    """由审批接口调用：写入决定并唤醒后台任务。

    返回是否确实有等待中的调用被唤醒（没有说明这条已经处理过或已超时）。
    """
    pending = _pending.get(tool_call_id)
    if pending is None or pending.decision:
        return False
    pending.decision = decision
    pending.remember = remember
    # "本回合记住"由调用方（loop）写进它自己那份集合 —— 集合可能挂在 checkpoint 上、
    # 也可能是循环里的局部变量（没启用 checkpoint 时），这里拿不到那份引用。
    checkpoint = find_by_tool_call(tool_call_id)
    if checkpoint is not None:
        checkpoint.status = "running"
    pending.event.set()
    return True


def release_pending(pending: PendingApproval) -> None:
    """等待结束后收尾（无论用户点了什么、还是超时）。"""
    _pending.pop(pending.tool_call_id, None)
    for checkpoint in _turns.values():
        if checkpoint.pending is pending:
            checkpoint.pending = None
            break


def finish(run_id: int) -> None:
    """回合结束：从表里移除。结果已经落进 messages 表，前端从库里读即可。"""
    checkpoint = _turns.pop(run_id, None)
    if checkpoint is not None and checkpoint.pending is not None:
        _pending.pop(checkpoint.pending.tool_call_id, None)


def sweep_orphans(now: float | None = None) -> int:
    """清掉"等了太久、用户显然不会回来"的回合，防内存永久驻留。

    在服务启动时或定时调用。返回清掉的条数。
    """
    current = now if now is not None else time.monotonic()
    stale = [
        run_id
        for run_id, item in _turns.items()
        if item.status == "waiting_approval" and current - item.created_at > ORPHAN_AFTER_S
    ]
    for run_id in stale:
        finish(run_id)
        logger.info("清理孤儿 checkpoint：run #%s", run_id)
    return len(stale)


def snapshot() -> dict[str, Any]:
    """诊断用：当前有多少回合、多少在等确认。"""
    return {
        "turns": len(_turns),
        "waiting": sum(1 for item in _turns.values() if item.status == "waiting_approval"),
        "buffered_events": sum(len(item.events) for item in _turns.values()),
    }
