"""文件文本读取：`.txt`、`.md` 直读，PDF 走文本层提取（不做 OCR）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import ToolContext
from app.tools.registry import tool

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


class PdfExtractError(RuntimeError):
    pass


def extract_text(pdf_path: str | Path) -> str:
    """读取简历正文；扫描件（无文本层）明确拒绝，不做 OCR。"""
    path = Path(pdf_path)
    if not path.exists():
        raise PdfExtractError(f"文件不存在：{path}")

    if path.suffix.lower() in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 50:
            raise PdfExtractError("文本过短（不足 50 字），请检查内容是否完整。")
        return text

    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24 的新包名
    except ImportError:  # pragma: no cover - 兼容旧版本
        import fitz  # type: ignore[no-redef]

    chunks: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            chunks.append(page.get_text("text"))
    text = "\n".join(chunks).strip()
    if len(text) < 50:
        raise PdfExtractError(
            "这份 PDF 没有可提取的文字（可能是图片/扫描件）。"
            "请用 Word、WPS 或超级简历导出「文字版 PDF」后重新上传。"
        )
    return text


@tool(
    impl_key="pdf_extract",
    title="文件文本读取",
    description="按文件路径读取简历正文（PDF / txt / md），返回纯文本。",
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（附件详情里可查）"},
        },
        "required": ["path"],
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "summary": {"type": "string"},
            "data": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    },
)
async def run(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    args = input.get("args") or {}
    config = input.get("config") or {}
    path = str(args.get("path") or config.get("path") or "").strip()
    if not path:
        return {"ok": False, "summary": "缺少必填参数 path", "error": "missing_arguments"}

    try:
        resolved = resolve_upload_path(path)
    except PdfExtractError as exc:
        return {"ok": False, "summary": str(exc), "error": "path_not_allowed"}
    text = extract_text(resolved)
    return {"ok": True, "summary": f"提取到 {len(text)} 字", "data": {"text": text[:8000]}}


def resolve_upload_path(path: str) -> Path:
    """把路径限制在上传目录内。

    `path` 是模型给的参数，不限制的话一句 `{"path": "/etc/passwd"}` 就能读服务器上任意文件。
    相对路径按上传目录解析，绝对路径必须落在上传目录里。
    """
    from app.core.config import settings

    root = settings.upload_path.resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise PdfExtractError(f"只能读取上传目录内的文件：{path}")
    return resolved
