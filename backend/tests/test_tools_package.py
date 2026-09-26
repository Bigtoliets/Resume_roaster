"""工具包（D32.10）：代码注册实现，页面用 JSON 定义参数与配置。

这些测试不连数据库：跑的是注册表、schema 拼装、返回值归一化这类纯逻辑。
"""

from __future__ import annotations

import pytest

from app.agent.loop import build_tool_schemas
from app.db.models import Tool
from app.tools._llm_runner import render
from app.tools.registry import TOOLS, _normalize, get_impl, impl_status, list_impls


def make_tool(**kwargs: object) -> Tool:
    base: dict[str, object] = {
        "id": 1,
        "name": "kb_search_perf",
        "display_name": "招聘语料检索",
        "description": "",
        "impl_key": "kb_search",
        "params_schema": {},
        "returns_schema": {},
        "impl_config": {},
        "is_enabled": True,
    }
    base.update(kwargs)
    return Tool(**base)  # type: ignore[arg-type]


def test_expected_impls_registered() -> None:
    assert {
        "kb_search",
        "http_request",
        "pdf_extract",
        "interview_tech_lead",
        "interview_hr",
        "final_judge",
    } <= set(TOOLS)


def test_impl_meta_carries_three_defaults() -> None:
    for item in list_impls():
        assert item["title"] and item["description"]
        assert item["kind"] in {"llm", "builtin"}
        assert isinstance(item["params_schema"], dict)
        assert isinstance(item["returns_schema"], dict)
        assert isinstance(item["config"], dict)


def test_llm_impls_need_prompt() -> None:
    for key in ("interview_tech_lead", "interview_hr", "final_judge"):
        assert get_impl(key).needs_prompt is True  # type: ignore[union-attr]
    assert get_impl("kb_search").needs_prompt is False  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("impl_key", "expected"),
    [("kb_search", "ok"), ("not_written_yet", "missing"), ("", "unset")],
)
def test_impl_status(impl_key: str, expected: str) -> None:
    assert impl_status(make_tool(impl_key=impl_key)) == expected


def test_page_schema_is_authoritative() -> None:
    """页面把 query 改成 keyword：模型就该看到 keyword（D32.10，不做代码侧过滤）。"""
    tool = make_tool(
        description="需要了解目标岗位真实要求时调用",
        params_schema={
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "改成关键词了"}},
            "required": ["keyword"],
        },
    )
    schema = build_tool_schemas([tool])[0]
    assert schema["function"]["name"] == "kb_search_perf"
    assert "真实要求" in schema["function"]["description"]
    assert "keyword" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["required"] == ["keyword"]


def test_description_falls_back_to_impl() -> None:
    spec = get_impl("kb_search")
    schema = build_tool_schemas([make_tool(description="")])[0]
    assert schema["function"]["description"] == spec.description  # type: ignore[union-attr]


def test_empty_page_schema_still_yields_object() -> None:
    schema = build_tool_schemas([make_tool(params_schema={})])[0]
    assert schema["function"]["parameters"]["type"] == "object"


def test_unimplemented_tool_is_not_offered_to_model() -> None:
    assert build_tool_schemas([make_tool(impl_key="not_written_yet")]) == []


def test_disabled_tool_is_not_offered_to_model() -> None:
    assert build_tool_schemas([make_tool(is_enabled=False)]) == []


def test_normalize_keeps_contract_keys() -> None:
    result = _normalize({"ok": True, "summary": "命中 3 条", "data": {"hits": []}})
    assert result["ok"] is True
    assert result["summary"] == "命中 3 条"
    assert result["data"] == {"hits": []}


def test_normalize_wraps_business_fields_into_data() -> None:
    result = _normalize({"ok": True, "summary": "命中 3 条", "items": [1, 2, 3]})
    assert result["data"] == {"items": [1, 2, 3]}
    assert "items" not in {key for key in result if key != "data"}


def test_normalize_handles_non_dict_result() -> None:
    result = _normalize("done")
    assert result["ok"] is True
    assert result["summary"] == "done"


def test_prompt_variable_rendering() -> None:
    assert render("简历：{{resume}}｜要求：{{job}}", {"resume": "A", "job": "B"}) == (
        "简历：A｜要求：B"
    )
    assert '"a": 1' in render("{{payload}}", {"payload": {"a": 1}})
