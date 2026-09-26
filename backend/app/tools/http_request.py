"""HTTP 请求：调用外部只读接口。"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext
from app.tools.registry import tool


@tool(
    impl_key="http_request",
    title="HTTP 请求",
    description="调用外部 HTTP 接口（只读优先；写操作建议开启人工确认）。",
    config={"method": "GET", "url": "", "headers": {}, "timeout_s": 30},
    params_schema={
        "type": "object",
        "properties": {"payload": {"type": "object", "description": "请求体或查询参数"}},
    },
    returns_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "summary": {"type": "string"},
            "data": {"type": "object", "properties": {"status": {"type": "integer"}, "body": {}}},
        },
    },
)
async def run(input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from urllib.parse import urlparse

    import httpx

    args = input.get("args") or {}
    config = input.get("config") or {}

    method = str(config.get("method") or "GET").upper()
    url = str(config.get("url") or "").strip()
    if not url:
        return {
            "ok": False,
            "summary": "这个工具还没配置 URL：在「配置 JSON」里填 url",
            "error": "config_incomplete",
        }
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        return {
            "ok": False,
            "summary": f"只允许 http / https 协议，收到：{scheme or '（空）'}",
            "error": "invalid_url",
        }

    payload = args.get("payload")
    async with httpx.AsyncClient(timeout=int(config.get("timeout_s") or 30)) as client:
        response = await client.request(
            method,
            url,
            headers=config.get("headers") or {},
            json=payload if method != "GET" else None,
            params=payload if method == "GET" else None,
        )
    body = response.text[:4000]
    return {
        "ok": response.status_code < 400,
        "summary": f"HTTP {response.status_code}，返回 {len(response.text)} 字符",
        "data": {"status": response.status_code, "body": body},
    }
