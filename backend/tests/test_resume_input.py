"""简历输入：PDF 与纯文本两条路（D32 起归入 app/tools/pdf_extract.py）。"""

from __future__ import annotations

import pytest

from app.tools.pdf_extract import PdfExtractError as ResumeParseError
from app.tools.pdf_extract import extract_text

SAMPLE = "张小明\n求职意向：Java 后端开发实习生\n" + "熟悉 Java、Spring Boot、MySQL。" * 5


def test_reads_txt(tmp_path) -> None:
    path = tmp_path / "resume.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    assert "Spring Boot" in extract_text(path)


def test_reads_markdown(tmp_path) -> None:
    path = tmp_path / "resume.md"
    path.write_text("# 简历\n" + SAMPLE, encoding="utf-8")
    assert extract_text(path).startswith("# 简历") or "Spring Boot" in extract_text(path)


def test_too_short_text_rejected(tmp_path) -> None:
    path = tmp_path / "resume.txt"
    path.write_text("太短", encoding="utf-8")
    with pytest.raises(ResumeParseError):
        extract_text(path)


def test_missing_file_rejected(tmp_path) -> None:
    with pytest.raises(ResumeParseError):
        extract_text(tmp_path / "nope.txt")


def test_blank_pdf_is_rejected(tmp_path) -> None:
    """没有文本层的 PDF（扫描件）必须明确拒绝，而不是静默返回空。"""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    with pytest.raises(ResumeParseError):
        extract_text(path)
