"""文本协议解析（D42）：支持 Thought/Action 风格的提示词。

背景：提示词可能用"文本协议"（要求模型输出 `Action: {...}` JSON），也可能用原生
function calling。运行时两条路都要认，否则换提示词就会"一步卡住"。
"""

from __future__ import annotations

from app.agent.loop import parse_text_action


def test_parses_plain_action() -> None:
    text = 'Thought: 需要检索岗位要求。\nAction: {"tool":"kb_search", "input":{"query":"Java"}}'
    assert parse_text_action(text) == ("kb_search", {"query": "Java"})


def test_parses_chinese_colon_and_code_fence() -> None:
    text = 'Thought：先读简历。\n\n```json\nAction：{"tool":"pdf_extract","input":{"path":"a.pdf"}}\n```'
    assert parse_text_action(text) == ("pdf_extract", {"path": "a.pdf"})


def test_parses_nested_arguments() -> None:
    text = (
        'Action: {"tool":"kb_search","input":{"queries":["a","b"],'
        '"options":{"top_k":5,"nested":{"deep":true}}}}'
    )
    tool, args = parse_text_action(text)  # type: ignore[misc]
    assert tool == "kb_search"
    assert args["options"]["nested"]["deep"] is True


def test_braces_inside_strings_do_not_break_pairing() -> None:
    text = 'Action: {"tool":"echo","input":{"text":"这里的 } 不该结束 JSON"}}'
    assert parse_text_action(text) == ("echo", {"text": "这里的 } 不该结束 JSON"})


def test_finish_action_is_recognised() -> None:
    text = 'Thought: 信息够了。\nAction: {"tool":"finish","input":{"answer":"结论如下"}}'
    assert parse_text_action(text) == ("finish", {"answer": "结论如下"})


def test_returns_none_without_action() -> None:
    assert parse_text_action("这是一段普通的最终回答，没有工具调用。") is None
    assert parse_text_action("") is None


def test_returns_none_on_broken_json() -> None:
    assert parse_text_action('Action: {"tool":"kb_search", "input":') is None


def test_returns_none_when_tool_missing() -> None:
    assert parse_text_action('Action: {"input":{"query":"x"}}') is None


def test_non_dict_input_falls_back_to_empty() -> None:
    assert parse_text_action('Action: {"tool":"kb_search","input":"oops"}') == ("kb_search", {})
