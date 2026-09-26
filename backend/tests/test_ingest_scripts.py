"""语料导入脚本的关键逻辑：岗位族映射、id 生成、元数据抬头、超长切分。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest import MAX_CHARS, build_header, split_long_text, to_rows  # noqa: E402
from job_families import JobFamilyError, build_id, normalize, prefix_for  # noqa: E402


def test_normalize_aliases() -> None:
    assert normalize("大数据") == "大数据开发"
    assert normalize("大数据开发实习生") == "大数据开发"
    assert normalize("Java 后端") == "Java后端"
    assert normalize("数据分析师") == "数据分析"


def test_unknown_family_raises() -> None:
    with pytest.raises(JobFamilyError):
        normalize("嵌入式开发")


def test_id_prefixes_are_stable() -> None:
    assert prefix_for("大数据开发") == "bigdata"
    assert build_id("大数据开发", "jd_001") == "bigdata_jd_001"
    assert build_id("Java后端", "jd_004") == "java_jd_004"
    assert build_id("数据分析", "exp_005") == "data_exp_005"


def test_build_header_keeps_optional_metadata() -> None:
    header = build_header(
        {"company": "某大厂", "title": "大数据开发实习生", "publish_date": "2026-08-12"}
    )
    assert header.startswith("【") and header.endswith("】")
    assert "公司：某大厂" in header and "发布：2026-08-12" in header


def test_build_header_empty_when_no_metadata() -> None:
    assert build_header({"doc_id": "jd_001"}) == ""


def test_to_rows_prepends_header_and_builds_id() -> None:
    rows = to_rows(
        [
            {
                "doc_id": "jd_001",
                "job_family": "大数据开发",
                "raw_text": "岗位职责：略。任职要求：略。",
                "company": "某大厂",
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "bigdata_jd_001"
    # 源文件的 raw_text 映射到 Milvus 的 content 字段（sparse 由 BM25 Function 生成）
    assert rows[0]["content"].startswith("【公司：某大厂】")


def test_split_long_text_respects_limit() -> None:
    # 每段约 2400 字，6 段共 1.4 万字，远超 MAX_CHARS
    paragraphs = "\n".join(f"第{i}段" + "内容" * 1200 for i in range(6))
    chunks = split_long_text(paragraphs)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_CHARS for chunk in chunks)
    assert "".join(chunks).count("内容") == paragraphs.count("内容")


def test_short_text_is_not_split() -> None:
    assert split_long_text("短文本") == ["短文本"]
