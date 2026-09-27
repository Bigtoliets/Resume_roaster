"""进程内 checkpoint：暂停/唤醒的三态，以及"本回合记住"的语义。"""

from __future__ import annotations

import asyncio
import time

from app.agent.checkpoints import (
    PendingApproval,
    TurnCheckpoint,
    finish,
    get,
    register,
    register_pending,
    release_pending,
    resolve_pending,
    snapshot,
    sweep_orphans,
    wait_for_decision,
)


def make_pending(tool_call_id: int = 7, *, timeout_s: int = 60) -> PendingApproval:
    return PendingApproval(
        tool_call_id=tool_call_id,
        tool_name="kb_search_perf",
        tool_display="招聘语料检索",
        tool_hint="在绑定知识库里做混合检索",
        arguments={"queries": ["Java 后端 实习"]},
        created_at=time.monotonic(),
        timeout_s=timeout_s,
    )


def make_checkpoint(run_id: int = 1) -> TurnCheckpoint:
    return TurnCheckpoint(run_id=run_id, session_id=9, agent_id=36, agent_name="简历诊断官")


async def test_confirm_wakes_the_turn() -> None:
    checkpoint = make_checkpoint()
    register(checkpoint)
    pending = make_pending()
    register_pending(checkpoint, pending)
    assert checkpoint.status == "waiting_approval"

    # 模拟前端点"确认"
    assert resolve_pending(7, "approved", remember=True) is True
    assert await wait_for_decision(pending) == "approved"
    release_pending(pending)
    assert checkpoint.pending is None
    finish(checkpoint.run_id)
    assert get(checkpoint.run_id) is None


async def test_cancel_returns_cancelled() -> None:
    checkpoint = make_checkpoint(2)
    register(checkpoint)
    pending = make_pending(8)
    register_pending(checkpoint, pending)
    assert resolve_pending(8, "cancelled") is True
    assert await wait_for_decision(pending) == "cancelled"
    release_pending(pending)
    finish(2)


async def test_timeout_is_a_distinct_state() -> None:
    """超时必须能和"用户取消"区分：模型该做的反应不一样。"""
    pending = make_pending(9, timeout_s=0)
    assert await wait_for_decision(pending) == "timeout"


async def test_resolve_is_idempotent() -> None:
    checkpoint = make_checkpoint(3)
    register(checkpoint)
    pending = make_pending(10)
    register_pending(checkpoint, pending)
    assert resolve_pending(10, "approved") is True
    # 第二次点（重复提交 / 另一路请求）不该再唤醒一次
    assert resolve_pending(10, "cancelled") is False
    finish(3)


async def test_wait_returns_cancelled_when_event_set_without_decision() -> None:
    pending = make_pending(11)
    pending.event.set()  # 直接唤醒但没写 decision（异常情况）
    assert await wait_for_decision(pending) == "cancelled"


async def test_snapshot_and_sweep_orphans() -> None:
    checkpoint = make_checkpoint(4)
    checkpoint.status = "waiting_approval"
    checkpoint.created_at = time.monotonic() - 4000  # 超过孤儿阈值
    register(checkpoint)
    assert snapshot()["turns"] >= 1
    assert sweep_orphans() >= 1
    assert get(4) is None


async def test_concurrent_waiters_do_not_cross_talk() -> None:
    """两个回合各等各的，唤醒一个不该影响另一个。"""
    first, second = make_checkpoint(5), make_checkpoint(6)
    register(first)
    register(second)
    p1, p2 = make_pending(20), make_pending(21)
    register_pending(first, p1)
    register_pending(second, p2)

    resolve_pending(20, "approved")
    done = await asyncio.wait_for(wait_for_decision(p1), timeout=1)
    assert done == "approved"
    # 第二个还在等
    assert second.pending is not None and second.pending.decision == ""
    finish(5)
    finish(6)
