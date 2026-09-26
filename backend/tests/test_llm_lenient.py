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
