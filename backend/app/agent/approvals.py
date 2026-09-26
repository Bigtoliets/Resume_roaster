"""人工确认门（D31 / Q6-A）。

需要审批的工具执行前，循环会挂在这里等一个事件；前端点"批准/拒绝"后由
`POST /tool-calls/{id}/approve` 唤醒**同一个**循环。超时按拒绝处理。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_waiters: dict[int, asyncio.Event] = {}
_decisions: dict[int, bool] = {}


async def wait_for_approval(tool_call_id: int, timeout_s: float = 180) -> bool:
    """等待用户决定；超时返回 False（按拒绝处理，循环继续）。"""
    event = asyncio.Event()
    _waiters[tool_call_id] = event
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_s)
        return _decisions.pop(tool_call_id, False)
    except TimeoutError:
        logger.info("工具调用 #%s 审批超时，按拒绝处理", tool_call_id)
        return False
    finally:
        _waiters.pop(tool_call_id, None)


def resolve_approval(tool_call_id: int, approved: bool) -> bool:
    """由审批接口调用；返回是否确实有等待中的调用被唤醒。"""
    event = _waiters.get(tool_call_id)
    if event is None:
        return False
    _decisions[tool_call_id] = approved
    event.set()
    return True
