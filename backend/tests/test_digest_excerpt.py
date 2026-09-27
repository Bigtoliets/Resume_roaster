"""投影输入要取**工具真实输出**，不是给前端看的 200 字预览（D53）。

事故链条：`_llm_runner` 的 summary 是 `前 200 字 + …` → 被直接当成投影输入 →
投影把省略号读成"内容被截断" → 笔记里写"截断版/截断发生在工具输出存储层" →
主模型照着笔记反复回查、重跑同一个面试官工具，把 12 轮预算全耗光。
"""

from __future__ import annotations

from app.agent.loop import DIGEST_EXCERPT_CHARS, digest_entry_text


def test_prefers_real_output_over_ui_summary() -> None:
    """有 `data.text` 时用它，绝不能退回那个带省略号的 summary。"""
    observation = {
        "ok": True,
        "summary": "## 致命问题（严重度 4-5）\n### 1. RAG 召回率…",
        "data": {"text": "## 致命问题（严重度 4-5）\n### 1. 完整内容" + "细节" * 20},
    }
    text = digest_entry_text(observation)
    assert text.startswith("## 致命问题（严重度 4-5）\n### 1. 完整内容")
    assert "…" not in text.split("（完整结果共")[0]  # 正文里不该带 UI 的省略号
    assert "（完整结果共" in text  # 标出真实总字数，投影才知道这是完整的


def test_structured_result_is_serialized_not_reduced_to_summary() -> None:
    """检索类工具没有 `data.text`，只有 `data.items` —— 不能只留一句 19 字摘要。

    实测踩过：投影看到"完整结果共 19 字"以为检索内容丢了，又去回查一遍。
    """
    observation = {
        "ok": True,
        "summary": "JD 语料库 4 个角度命中 12 次",
        "data": {"items": [{"id": "jd_037", "score": 0.61, "text": "3-5 年经验"}]},
    }
    text = digest_entry_text(observation)
    assert "jd_037" in text  # 命中的明细要带进去
    assert "（完整结果共 19 字）" not in text
    assert "（完整结果共" in text


def test_falls_back_to_summary_when_no_data() -> None:
    text = digest_entry_text({"ok": True, "summary": "命中 3 条"})
    assert text == f"命中 3 条（完整结果共 {len('命中 3 条')} 字）"


def test_long_output_is_excerpted_with_explicit_marker() -> None:
    long_text = "x" * (DIGEST_EXCERPT_CHARS + 500)
    text = digest_entry_text({"data": {"text": long_text}})
    assert "此处仅摘录" in text
    assert f"完整结果共 {len(long_text)} 字" in text
    assert len(text) < len(long_text)


def test_empty_observation_returns_empty() -> None:
    assert digest_entry_text({}) == ""
    assert digest_entry_text({"summary": "   "}) == ""
