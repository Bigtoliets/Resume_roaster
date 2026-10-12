"""重建脚本的关键性质：把已分块的语料拼回去时不叠抬头、不丢正文、可重复执行。

幂等这件事必须有用例盯着：线上语料本身就是分块后的产物，脚本一旦不幂等，
跑第二次就会把抬头叠一层、把每块当成独立岗位切碎——而且是**静默**毁掉语料。
"""

from __future__ import annotations

from scripts.rebuild_jd import merge, split_jobs, to_rows

HEADER = "【岗位 1】Java 后端开发工程师\n薪资范围：25-40K"


def _chunked_rows() -> list[dict[str, str]]:
    return [
        {
            "id": "jd_001#1",
            "content": f"{HEADER}\n岗位职责：\n1. 负责订单系统的接口开发与维护，保障线上稳定。",
        },
        {
            "id": "jd_001#2",
            "content": f"{HEADER}\n任职要求：\n1. 熟悉 JVM 调优与 Redis 缓存设计，有高并发经验。",
        },
    ]


def test_merge_joins_chunks_without_repeating_the_header() -> None:
    markdown = merge(_chunked_rows())
    assert markdown.count("【岗位 1】") == 1
    assert markdown.count("薪资范围：25-40K") == 1
    assert "岗位职责：" in markdown and "任职要求：" in markdown


def test_merge_keeps_single_row_documents_intact() -> None:
    markdown = merge(
        [{"id": "jd_007", "content": "【岗位 7】数据分析师\n岗位职责：\n1. 做报表。"}]
    )
    assert "【岗位 7】数据分析师" in markdown
    assert "1. 做报表。" in markdown


def test_rebuild_is_idempotent() -> None:
    first = to_rows(split_jobs(merge(_chunked_rows())))
    second = to_rows(split_jobs(merge(first)))
    assert [row["id"] for row in second] == [row["id"] for row in first]
    assert [row["content"] for row in second] == [row["content"] for row in first]
