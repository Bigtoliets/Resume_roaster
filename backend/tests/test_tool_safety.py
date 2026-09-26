"""工具层的两条安全底线（D36）：文件读取限制在上传目录、HTTP 只走 http/https。"""

from __future__ import annotations

import pytest

from app.db.models import Tool
from app.tools.base import ToolContext
from app.tools.http_request import run as http_run
from app.tools.pdf_extract import PdfExtractError, resolve_upload_path


def test_accepts_path_inside_uploads() -> None:
    from app.core.config import settings

    root = settings.upload_path.resolve()
    resolved = resolve_upload_path("demo.pdf")
    assert resolved == root / "demo.pdf"
    assert resolved.is_relative_to(root)


@pytest.mark.parametrize(
    "path",
    [
        "C:/Windows/System32/drivers/etc/hosts",
        "../../../etc/passwd",
        "/etc/shadow",
    ],
)
def test_rejects_path_outside_uploads(path: str) -> None:
    with pytest.raises(PdfExtractError):
        resolve_upload_path(path)


def make_ctx(config: dict) -> ToolContext:
    tool = Tool(name="probe", display_name="探针", impl_key="http_request", impl_config=config)
    return ToolContext(tool=tool, config=config, db=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "ftp://host/f", ""])
async def test_http_rejects_non_http_scheme(url: str) -> None:
    ctx = make_ctx({"url": url, "method": "GET"})
    result = await http_run({"args": {}, "config": ctx.config}, ctx)
    assert result["ok"] is False
    assert result.get("error") in {"invalid_url", "config_incomplete"}
