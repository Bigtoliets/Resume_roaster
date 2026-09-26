"""Langfuse 连通性验证：发一条真实 trace，能在 UI 里看到才算接通（方案 §9.5）。

用法：
    python scripts/check_langfuse.py
"""

from __future__ import annotations

import sys
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import settings
from app.obs.langfuse import trace_url


def main() -> int:
    if not settings.langfuse_enabled:
        print("Langfuse 未配置（需要 LANGFUSE_BASE_URL / PUBLIC_KEY / SECRET_KEY）。")
        return 2

    project = settings.langfuse_project_id or "未填写"
    print(f"目标：{settings.langfuse_url}（project={project}）")
    try:
        from langfuse import Langfuse
    except Exception as exc:  # noqa: BLE001
        print(f"未安装 langfuse SDK：{exc}\n安装：pip install 'langfuse>=3'")
        return 2

    try:
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_url,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Langfuse 客户端初始化失败：{type(exc).__name__}: {exc}")
        return 1

    marker = f"resume-roaster-check-{uuid.uuid4().hex[:8]}"
    try:
        # langfuse v4 的写法：trace() / trace.span() 是 2.x 的 API，已经不存在
        with client.start_as_current_observation(
            name="connectivity-check", input={"marker": marker}
        ) as span:
            trace_id = client.get_current_trace_id()
            span.update(output={"ok": True, "marker": marker})
        client.flush()
    except Exception as exc:  # noqa: BLE001
        print(f"写入 trace 失败：{type(exc).__name__}: {exc}")
        return 1

    print(f"已发送 trace：{trace_id}")
    time.sleep(1)
    print(f"打开确认：\n  {trace_url(trace_id)}")
    print(
        "\n注意：Langfuse v4 起服务端跑在 events_only 模式，"
        "写入走 OTLP（/api/public/otel/v1/traces），原来的 /api/public/traces 查询接口已下线，"
        "所以只能用上面的链接去 UI 里看，不能再拿 REST 接口反查。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
