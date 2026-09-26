"""日志：JSON 结构化输出，带 session_id / run_id / node 上下文。"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

_EMPTY: Mapping[str, Any] = MappingProxyType({})
_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default=_EMPTY)


def bind_context(**kwargs: Any) -> None:
    """绑定本次请求/本次运行的上下文（session_id、run_id、node 等）。"""
    _context.set({**_context.get(), **kwargs})


def clear_context() -> None:
    _context.set(_EMPTY)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context.get())
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    # Windows 控制台默认 GBK，中文日志会乱码：强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # 降噪
    for noisy in ("httpx", "httpcore", "urllib3", "openai._base_client"):
        logging.getLogger(noisy).setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
