"""追问轮的续接与文案注入（Q2 / Q10 / Q16）。

`pending_slot` 是 Q16 的关键：用户回答"大数据开发实习生"时没有触发词，
全靠上一条追问消息把它认出来，所以这里的判定错一次，追问功能就整条失效。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.api.v1.sessions_router import _compose_turn_text, _pending_slot


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalars(self) -> _FakeResult:
        return self

    def first(self) -> Any:
        return self._row


class _FakeDB:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._row)


async def test_pending_slot_reads_the_last_ask() -> None:
    row = SimpleNamespace(content={"kind": "ask", "slot": "target_role"})
    assert await _pending_slot(_FakeDB(row), 1) == "target_role"


async def test_pending_slot_ignores_a_normal_answer() -> None:
    row = SimpleNamespace(content={"text": "你的简历我已经看完了", "steps": []})
    assert await _pending_slot(_FakeDB(row), 1) == ""


async def test_pending_slot_survives_odd_content() -> None:
    assert await _pending_slot(_FakeDB(None), 1) == ""
    assert await _pending_slot(_FakeDB(SimpleNamespace(content="not a dict")), 1) == ""


def test_compose_turn_text_only_injects_a_real_role() -> None:
    assert _compose_turn_text("好的", "") == "好的"
    assert _compose_turn_text("帮我看看", "大数据开发实习生") == (
        "【本轮输入】\n目标岗位：大数据开发实习生\n\n帮我看看"
    )


def test_compose_turn_text_handles_attachment_only_turn() -> None:
    assert _compose_turn_text("", "大数据开发实习生") == "【本轮输入】\n目标岗位：大数据开发实习生"
