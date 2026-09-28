"""文本协议解析（D42）：支持 Thought/Action 风格的提示词。

背景：提示词可能用"文本协议"（要求模型输出 `Action: {...}` JSON），也可能用原生
function calling。运行时两条路都要认，否则换提示词就会"一步卡住"。
"""

from __future__ import annotations

from app.agent.loop import parse_text_action, strip_protocol_scaffolding


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


# ---------------------------------------------------------------- 宽松解析


def test_parses_bold_action_with_fence_after_colon() -> None:
    """实测踩过的形状：加粗 Action + 冒号后换行 + ```json 围栏。

    这种文本以前解析不出来，于是整轮被当成"最终回答"，裸协议进了答案气泡。
    """
    text = (
        "**Thought**：用户的显性诉求是成为 Agent 工程师。\n\n"
        "**Action**：\n```json\n"
        '{"tool":"kb_search_perf","input":{"queries":["a","b"]}}\n'
        "```"
    )
    assert parse_text_action(text) == ("kb_search_perf", {"queries": ["a", "b"]})


def test_parses_fence_without_bold() -> None:
    text = 'Action:\n```json\n{"tool":"recall","input":{"pointers":["W1.T2"]}}\n```'
    assert parse_text_action(text) == ("recall", {"pointers": ["W1.T2"]})


# ---------------------------------------------------------------- 展示去壳


def test_strip_keeps_only_thought_body() -> None:
    text = 'Thought: 先读简历正文。\n\nAction: {"tool":"pdf_extract","input":{"path":"a.pdf"}}'
    assert strip_protocol_scaffolding(text) == "先读简历正文。"


def test_strip_handles_bold_label_and_fenced_action() -> None:
    text = '**Thought**：先检索岗位要求。\n\n**Action**：\n```json\n{"tool":"kb_search"}\n```'
    assert strip_protocol_scaffolding(text) == "先检索岗位要求。"


def test_strip_leaves_plain_answer_untouched() -> None:
    """页面只展示一种形态：链路。但普通回答不该被误伤。"""
    text = "这是一段普通回答。\n\n第二段。"
    assert strip_protocol_scaffolding(text) == text


def test_strip_action_only_text_collapses_to_empty() -> None:
    """只有 Action、没有思考正文时返回空串，调用方据此不产生空的思考卡片。"""
    assert strip_protocol_scaffolding('Action: {"tool":"kb_search"}') == ""
