"""Langfuse 观测：整次诊断一个 trace、每个阶段一个 span、LLM 调用自动采集。

铁律：观测系统是**旁路**。任何一步失败都必须静默降级——trace 图标置灰，
诊断照常出报告，绝不让可观测性把主流程拖垮（方案 §9.5）。
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_initialized = False
_retry_after = 0.0

#: 最近一次初始化失败的原因，供体检接口展示（成功时清空）。
_last_error = ""

#: 初始化失败后的冷却秒数：冷却期内静默降级，过后自动重试。
#: **不能永久降级** —— 之前把"失败"也写进 _initialized，一次网络抖动
#: （或换了 key 没重启）就会让整个进程生命周期内再也不产生 trace。
FAILURE_COOLDOWN_S = 60.0


def get_client() -> Any | None:
    """懒加载单例。未配置返回 None；初始化失败进入冷却，冷却结束会自动重试。"""
    global _client, _initialized, _retry_after, _last_error
    if _client is not None:
        return _client
    if not settings.langfuse_enabled:
        logger.info("Langfuse 未配置，观测降级为关闭")
        return None
    if _initialized and time.monotonic() < _retry_after:
        return None  # 冷却期内不再打网络，避免每个请求都去探活
    _initialized = True
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_url,
        )
        # 真实探活：服务没起来时不要让报告里出现打不开的 trace 链接
        if not client.auth_check():
            raise RuntimeError(f"探活失败（{settings.langfuse_url}）")
        _client = client
        _retry_after = 0.0
        _last_error = ""
        logger.info("Langfuse 已接入：%s", settings.langfuse_url)
    except Exception as exc:  # noqa: BLE001
        _client = None
        _retry_after = time.monotonic() + FAILURE_COOLDOWN_S
        _last_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "Langfuse 初始化失败，已降级（%.0fs 后自动重试）：%s", FAILURE_COOLDOWN_S, exc
        )
    return _client


def client_status() -> dict[str, Any]:
    """体检用：**真去拿一次客户端**，而不是只读配置开关。

    只报 `configured` 会骗人 —— 依赖没装（`No module named 'langfuse'`）、
    服务不可达、key 不对，这三种都会让观测**静默关闭**，而 `configured` 依旧是真。
    实测就踩过：换了个没装 SDK 的 Python 环境跑，整条链路在 Langfuse 上凭空消失，
    而 `/system/status` 一直说配置正常，查了半天才发现是环境问题。

    `get_client()` 自带 60s 失败冷却，所以这里不会被体检请求打爆。
    """
    if not settings.langfuse_enabled:
        return {
            "configured": False,
            "ok": False,
            "error": "未配置 LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL",
            "url": "",
        }
    client = get_client()
    return {
        "configured": True,
        "ok": client is not None,
        "error": "" if client is not None else (_last_error or "初始化失败（详见后端日志）"),
        "url": settings.langfuse_public_url,
    }


@contextmanager
def observation(name: str, *, as_type: str | None = None, **kwargs: Any) -> Iterator[Any]:
    """开启一个 observation；未配置时退化为 nullcontext，业务代码无需分支。

    `as_type` 用 v4 的语义化类型（`tool` / `agent` / `generation`…），
    UI 会把它们渲染成对应形态（比如工具调用带参数与结果）。旧版本 SDK 不支持时自动回退。
    """
    client = get_client()
    if client is None:
        yield None
        return
    try:
        if as_type:
            try:
                manager = client.start_as_current_observation(
                    name=name, as_type=as_type, **kwargs
                )
            except Exception:  # noqa: BLE001 - SDK 版本不支持语义化类型就退回普通 span
                manager = client.start_as_current_observation(name=name, **kwargs)
        else:
            manager = client.start_as_current_observation(name=name, **kwargs)
        span = manager.__enter__()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse observation 创建失败，已降级：%s", exc)
        yield None
        return
    try:
        yield span
    finally:
        with contextlib.suppress(Exception):
            manager.__exit__(None, None, None)


def update_current_generation(**kwargs: Any) -> None:
    """覆盖自动采集到的 generation 字段。

    场景：OpenAI 包装器会把**整个请求 payload**（含几十 KB 的 tools 定义）当成 input 记录，
    我们只想留 messages，就把 input 覆盖成精简版，并把 tool_calls 明确写进 output。
    静默降级：观测是旁路，任何一步失败都不能影响主流程。
    """
    client = get_client()
    if client is None:
        return
    try:
        client.update_current_generation(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse generation 覆盖失败：%s", exc)


@contextmanager
def propagated(
    *, session_id: str = "", user_id: str = "", tags: list[str] | None = None
) -> Iterator[None]:
    """把 session_id / user_id / tags 传播给内部所有 observation。

    官方 v4 的写法是 `propagate_attributes(...)`：多轮对话里每轮是一个独立 trace，
    靠 `session_id` 把它们聚成一次会话，Langfuse 的 Sessions 视图才串得起来。
    未配置或 SDK 不支持时退化为空上下文。
    """
    client = get_client()
    if client is None:
        yield
        return
    try:
        from langfuse import propagate_attributes

        with propagate_attributes(
            session_id=session_id or None,
            user_id=user_id or None,
            tags=tags or None,
        ):
            yield
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse 属性传播失败，已降级：%s", exc)
        yield


def current_trace_id() -> str:
    client = get_client()
    if client is None:
        return ""
    try:
        return str(client.get_current_trace_id() or "")
    except Exception:  # noqa: BLE001
        return ""


def start_span(name: str, **kwargs: Any) -> Any | None:
    client = get_client()
    if client is None:
        return None
    try:
        return client.start_observation(name=name, as_type="span", **kwargs)
    except Exception:  # noqa: BLE001
        return None


def end_span(span: Any | None, **kwargs: Any) -> None:
    if span is None:
        return
    with contextlib.suppress(Exception):
        span.end(**kwargs)


def flush() -> None:
    client = get_client()
    if client is None:
        return
    with contextlib.suppress(Exception):
        client.flush()


def trace_url(trace_id: str) -> str:
    """trace 跳转链接：优先问 SDK 要（版本无关），失败时退回配置模板。"""
    if not trace_id:
        return ""
    client = get_client()
    if client is None:
        # 服务不可用时不产出链接，避免报告里出现打不开的 trace 图标
        return ""
    with contextlib.suppress(Exception):
        url = client.get_trace_url(trace_id=trace_id)
        if url:
            return str(url)
    if not settings.langfuse_public_url:
        return ""
    return settings.langfuse_trace_template.format(
        host=settings.langfuse_public_url,
        project_id=settings.langfuse_project_id,
        trace_id=trace_id,
    )
