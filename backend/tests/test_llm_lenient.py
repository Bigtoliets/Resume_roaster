"""宽松 JSON 解析与重试指令（方案 §4.6）。"""

from __future__ import annotations

import pytest

from app.llm.client import _loads_lenient
from app.schemas.resource import ToolImplOut


def test_strips_code_fence() -> None:
    raw = '```json\n{"decision": "continue"}\n```'
    assert _loads_lenient(raw) == {"decision": "continue"}


def test_extracts_json_from_prose() -> None:
    raw = '好的，结果如下：\n{"decision": "terminate", "reason": "已覆盖"}\n希望有帮助。'
    assert _loads_lenient(raw)["decision"] == "terminate"


def test_rejects_empty_and_non_object() -> None:
    with pytest.raises(ValueError):
        _loads_lenient("   ")
    with pytest.raises(ValueError):
        _loads_lenient("[1, 2, 3]")


def test_schema_json_contains_fields() -> None:
    """_retry_instruction 依赖的 model_json_schema 能正常产出（用任意模型验证）。"""
    schema = ToolImplOut.model_json_schema()
    assert "properties" in schema and "impl_key" in schema["properties"]


async def test_complete_json_forwards_generation_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`name` 要一路传到真正的调用上 —— 否则 Langfuse 里所有辅助调用都叫 llm-call，分不清。"""
    from app.agent.notes import Notes
    from app.llm.client import LLMClient

    client = LLMClient(base_url="http://127.0.0.1:1/v1", api_key="sk-test", model="probe")
    seen: list[str] = []

    async def fake_complete(
        messages: list[dict[str, str]], *, json_mode: bool = False, name: str = "llm-call"
    ) -> str:
        seen.append(name)
        return (
            '{"goal": "诊断简历",'
            ' "progress": {"stage": "已读简历", "done": [], "missing": []},'
            ' "plan": [], "refs": []}'
        )

    monkeypatch.setattr(client, "complete", fake_complete)
    result = await client.complete_json(
        [{"role": "user", "content": "x"}], Notes, name="notes-projection"
    )
    assert seen == ["notes-projection"]
    assert result.goal == "诊断简历"
