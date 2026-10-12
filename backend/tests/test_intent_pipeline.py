"""意图层主管道（Q5 / Q8 / Q12 / Q16）：级联、降级、追问判定、pending 续接。"""

from __future__ import annotations

from typing import Any

import pytest

from app.intent import pipeline
from app.intent.config import SLOT_TARGET_ROLE, Intent
from app.intent.pipeline import _Extraction, resolve


def _fake_extract(monkeypatch: pytest.MonkeyPatch, result: _Extraction | None) -> list[str]:
    """把 L3 换成假实现，返回一个记录调用次数的列表（用来断言"没调模型"）。"""
    calls: list[str] = []

    async def fake(text: str) -> _Extraction | None:
        calls.append(text)
        return result

    monkeypatch.setattr(pipeline, "_extract", fake)
    return calls


def _explode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_text: str) -> Any:
        raise AssertionError("这条不该走到 L3")

    monkeypatch.setattr(pipeline, "_extract", boom)


async def test_slot_rule_short_circuits_the_whole_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q20：槽位规则命中就短路，不该再花一次模型调用。"""
    _explode(monkeypatch)

    result = await resolve(text="目标岗位是大数据开发实习生")

    assert result.source == "rules"
    assert result.target_role == "大数据开发实习生"
    assert result.ask is False


async def test_no_trigger_word_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q11：门卫没命中就完全不介入，连 L3 都不碰。"""
    _explode(monkeypatch)

    result = await resolve(text="好的，继续")

    assert result.source == "none"
    assert result.target_role == ""
    assert result.ask is False


async def test_diagnose_without_role_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_extract(
        monkeypatch,
        _Extraction(intent="diagnose", confidence=0.9, target_role=None),
    )

    result = await resolve(text="帮我看看这份简历")

    assert result.intent == Intent.DIAGNOSE.value
    assert result.ask is True
    assert result.slot == SLOT_TARGET_ROLE


async def test_inherited_role_beats_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q8：继承优先于追问——第二轮没提岗位也不该再问一遍。"""
    _fake_extract(
        monkeypatch,
        _Extraction(intent="diagnose", confidence=0.9, target_role=None),
    )

    result = await resolve(text="再帮我看看项目经历", inherited_role="大数据开发实习生")

    assert result.ask is False
    assert result.target_role == ""


async def test_role_outside_the_original_text_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q12：岗位必须是原句子串，模型编出来的一律丢弃，宁可追问。"""
    _fake_extract(
        monkeypatch,
        _Extraction(intent="diagnose", confidence=0.9, target_role="大数据开发实习生"),
    )

    result = await resolve(text="帮我看看这份简历")

    assert result.target_role == ""
    assert result.ask is True


async def test_pending_slot_bypasses_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q16：用户回答"大数据开发实习生"时没有触发词，靠 pending 放行。"""
    calls = _fake_extract(
        monkeypatch,
        _Extraction(intent="chitchat", confidence=0.2, target_role="大数据开发实习生"),
    )

    result = await resolve(text="大数据开发实习生", pending_slot=SLOT_TARGET_ROLE)

    assert calls == ["大数据开发实习生"]
    assert result.target_role == "大数据开发实习生"
    assert result.ask is False


async def test_pending_slot_never_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q16：回答没抽出岗位也不再追问，避免无限往返。"""
    _fake_extract(monkeypatch, _Extraction(intent="diagnose", confidence=0.9, target_role=None))

    result = await resolve(text="还没想好", pending_slot=SLOT_TARGET_ROLE)

    assert result.ask is False


async def test_extraction_failure_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q5：L3 挂了就是空槽位放行，绝不阻塞主循环。"""
    _fake_extract(monkeypatch, None)

    result = await resolve(text="帮我看看这份简历")

    assert result.target_role == ""
    assert result.ask is False
