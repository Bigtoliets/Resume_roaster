"""语料清洗与切块（app/vector/chunking.py）。

线上那批 JD 是"定长切分 + 拼回整篇"留下的，重复段和粘成一行的小标题
直接拖垮了检索质量。这里的每条用例都对应一种真实出现过的脏形态。
"""

from __future__ import annotations

from app.vector.chunking import chunk_document, dedupe_blocks, normalize_sections, split_sections


def test_dedupe_removes_contiguous_repeated_block() -> None:
    lines = ["岗位职责：", "", "1. 开发", "", "2. 调优", "岗位职责：", "", "1. 开发", "", "2. 调优", "3. 上线"]
    assert dedupe_blocks(lines) == ["岗位职责：", "", "1. 开发", "", "2. 调优", "3. 上线"]


def test_normalize_breaks_a_header_glued_to_the_previous_line() -> None:
    """真实形态："…研发工作量任职要求：" —— 标题粘在上一句末尾。"""
    text = "5. 参与需求评审，评估技术可行性与研发工作量任职要求：\n1. 精通 JVM"
    assert normalize_sections(text).split("\n") == [
        "5. 参与需求评审，评估技术可行性与研发工作量",
        "任职要求：",
        "1. 精通 JVM",
    ]


def test_normalize_does_not_break_a_normal_sentence() -> None:
    """只在标签后面跟着冒号时才断行，"明确岗位职责"这类正常说法不能切。"""
    text = "入职后要明确岗位职责与分工"
    assert normalize_sections(text) == text


def test_dedupe_survives_a_glued_tail() -> None:
    """重复块的**最后一行**粘着下一段标题时，先断行才能认出整块重复。"""
    text = (
        "加分项：\n1. 有政务项目经验\n2. 熟悉国产数据库\n"
        "加分项：\n1. 有政务项目经验\n2. 熟悉国产数据库其他说明：五险一金"
    )
    lines = dedupe_blocks(normalize_sections(text).split("\n"))
    joined = "\n".join(lines)
    assert joined.count("有政务项目经验") == 1
    assert "其他说明：五险一金" in joined


def test_split_sections_groups_by_heading() -> None:
    sections = split_sections("岗位职责：\n1. 开发\n任职要求：\n1. 精通 Java")
    assert len(sections) == 2
    assert sections[0].startswith("岗位职责：")
    assert sections[1].startswith("任职要求：")


def test_chunk_document_repeats_the_header_on_every_chunk() -> None:
    chunks = chunk_document(
        "岗位职责：\n1. 负责订单系统的接口开发与维护，保障线上稳定性。\n"
        "任职要求：\n1. 熟悉 JVM 调优与 Redis 缓存设计，有高并发场景经验。",
        header="【公司：某大厂】",
    )
    assert len(chunks) == 2
    assert all(chunk.startswith("【公司：某大厂】") for chunk in chunks)


def test_chunk_document_merges_tiny_tail_sections() -> None:
    """只剩标题的碎块并进上一块，别产出"加分项："这种独立块。"""
    chunks = chunk_document("任职要求：\n1. 精通 Java 并发与 JVM 调优，能定位线上问题。\n加分项：")
    assert len(chunks) == 1
    assert "加分项：" in chunks[0]


def test_chunk_document_respects_max_chars() -> None:
    body = "任职要求：\n" + "\n".join(f"{i}. " + "内容" * 60 for i in range(12))
    chunks = chunk_document(body, max_chars=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_chunk_document_returns_nothing_for_blank_text() -> None:
    assert chunk_document("   \n\n  ") == []
