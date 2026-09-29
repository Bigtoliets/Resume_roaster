"""取一个回合的完整轨迹（SD1–SD5）。

Langfuse v4 只有一条读路径可用：

    GET /api/public/v2/observations?trace_id=<id>&fields=core,basic,io,...

三个实测结论（详见 docs/skill-distillation-plan.md §3）：

1. **不传 `fields` 就没有内容** —— v2 默认只返回 `core` + `basic`，`input`/`output`
   全是 `null`。这会被误判成"Langfuse 里没存东西"。
2. **io 是原始字符串而不是对象**，要自己 `json.loads`；`parseIoAsJson=true` 在 v2 上
   已废弃，传了直接 400。
3. **写入后约 2 秒才可见**（实测首次命中的 2.13s），所以必须重试。

另外：v3 风格的端点（`/api/public/traces`、`/observations`、`/sessions`、`/metrics`）
在 events_only 模式下**全部 404**，而且给不存在的 trace id 也返回同一句 404 —— 那是
路由级门禁，加任何参数都没用，别再试。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 要哪些字段组。**必须显式传**，否则 io 拿不到。
FIELD_GROUPS = "core,basic,io,metadata,usage,model,metrics,trace_context,prompt,time"

#: 保留的字段（SD5）。恒空的 22 个字段直接丢；metadata / trace_name / project_id
#: / environment / bookmarked / public 也丢（要么是 OTel 噪音，要么是固定值）。
KEEP_FIELDS = (
    "id",
    "trace_id",
    "type",
    "name",
    "parent_observation_id",
    "start_time",
    "end_time",
    "latency",
    "model",
    "usage_details",
    "session_id",
    "level",
)

#: 重试策略：实测 2.13s 才可见，5 × 0.5s 覆盖得住
FETCH_ATTEMPTS = 5
FETCH_INTERVAL_S = 0.5

#: 一次取多少条（一个回合的 observation 是十几个量级）
FETCH_LIMIT = 100


def _maybe_json(value: Any) -> Any:
    """io 是原始字符串；看着像 JSON 才解析，失败原样返回（绝不猜）。"""
    if not isinstance(value, str):
        return _jsonable(value)
    text = value.strip()
    if text[:1] in "[{" and text[-1:] in "]}":
        try:
            return _jsonable(json.loads(text))
        except json.JSONDecodeError:
            return value
    return value


def _jsonable(value: Any) -> Any:
    """把 datetime / date 转成 ISO 字符串，让整份轨迹可以直接 `json.dumps`。

    plain `model_dump()` 保留 datetime 对象（不可序列化），而 `mode="json"` 虽然会转成
    字符串、却**无视 by_alias=False 一律输出 camelCase 键**（startTime），prune 会
    全部认不出来。所以这里自己转，键名与值都保住。
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def prune(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留 KEEP_FIELDS，并把 io 解析成对象；按时间排序。"""
    # 防呆：拿到 camelCase 键说明用了别名序列化（SDK 的 mode="json" 默认如此），
    # 那样 start_time / trace_id / session_id 全会被当成"不认识的键"静默丢掉，
    # 结果是一份几乎没有元数据的轨迹 —— 没人会立刻发现，但判断依据已经残缺。
    if observations and "startTime" in observations[0] and "start_time" not in observations[0]:
        raise RuntimeError(
            "轨迹字段是 camelCase：model_dump 用了别名。"
            "请用 model_dump(mode='json', by_alias=False) 取蛇形字段名。"
        )

    out: list[dict[str, Any]] = []
    for obs in observations:
        item: dict[str, Any] = {}
        for key in KEEP_FIELDS:
            value = obs.get(key)
            if value is None or value == "":
                continue
            item[key] = _jsonable(value)
        for key in ("input", "output"):
            value = obs.get(key)
            if value:
                item[key] = _maybe_json(value)
        out.append(item)
    out.sort(key=lambda o: str(o.get("start_time") or ""))
    return out


def _fetch_once(trace_id: str) -> list[dict[str, Any]]:
    """一次同步拉取。langfuse SDK 是同步库，调用方负责放到线程里跑。"""
    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_url,
    )
    response = client.api.observations.get_many(
        trace_id=trace_id, limit=FETCH_LIMIT, fields=FIELD_GROUPS
    )
    # ⚠️ 这里只能用 plain `model_dump()`：实测 `mode="json"` 无视 `by_alias=False`，
    # 一律输出 camelCase 键（startTime / traceId / usageDetails），会被 prune 静默丢掉。
    # 时间对象由 `_jsonable()` 自己转 ISO —— 键名与序列化两头都要。
    raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    return list(raw.get("data") or [])


async def fetch_trace(
    trace_id: str,
    *,
    attempts: int = FETCH_ATTEMPTS,
    interval_s: float = FETCH_INTERVAL_S,
) -> list[dict[str, Any]] | None:
    """按 trace_id 取一个回合的全部 observation（已削减字段）。取不到返回 None。

    SDK 是同步的，所以每次尝试都丢进线程里跑，避免阻塞事件循环 —— 这个函数是在
    后台任务里被调用的，不能把整个 uvicorn 卡住。
    """
    if not trace_id:
        return None
    if not settings.langfuse_enabled:
        logger.info("Langfuse 未配置，取不到轨迹（trace=%s）", trace_id)
        return None

    last_error = ""
    for attempt in range(max(1, attempts)):
        try:
            data = await asyncio.to_thread(_fetch_once, trace_id)
            if data:
                return prune(data)
        except Exception as exc:  # noqa: BLE001 - 取不到就降级，绝不抛出
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < attempts - 1:
            await asyncio.sleep(interval_s)

    logger.info(
        "轨迹未取到（trace=%s，重试 %s 次仍为空）%s",
        trace_id,
        attempts,
        f"：{last_error}" if last_error else "",
    )
    return None
