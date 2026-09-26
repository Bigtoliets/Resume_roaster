"""本地内存缓存：TTLCache（带 TTL 过期 + LRU 淘汰），用于数据库查询结果。

读：先查缓存 → 命中直接返回；未命中查 DB 并写入缓存。
写：**先操作数据库，成功后再删除对应缓存 key**（保证失败时不脏读）。

注意：cachetools 的 TTLCache 官方声明**非线程安全**。本项目后端是单事件循环（asyncio），
同一时刻只有一处读写，所以直接用它没问题；将来若引入多线程 worker，需要换成带锁的实现。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cachetools import TTLCache

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: 最多 500 条、每条存活 60 秒（用户指定）
db_cache: TTLCache[str, Any] = TTLCache(maxsize=500, ttl=60)

#: 便于统计命中率，排查"为什么没走缓存"
stats = {"hit": 0, "miss": 0, "invalidate": 0}


def cache_get(key: str) -> Any | None:
    value = db_cache.get(key)
    if value is None:
        stats["miss"] += 1
    else:
        stats["hit"] += 1
    return value


def cache_set(key: str, value: Any) -> None:
    db_cache[key] = value


def cache_delete(*keys: str) -> None:
    for key in keys:
        if db_cache.pop(key, None) is not None:
            stats["invalidate"] += 1


def invalidate_prefix(prefix: str) -> int:
    """按前缀批量失效（例如某会话被改动时清掉它的所有缓存）。"""
    removed = 0
    for key in list(db_cache.keys()):
        if key.startswith(prefix):
            db_cache.pop(key, None)
            removed += 1
    stats["invalidate"] += removed
    return removed


def clear_all() -> None:
    db_cache.clear()


async def cached(key: str, loader: Callable[[], Awaitable[T]]) -> T:
    """读路径：命中直接返回，否则执行 loader 并写缓存。"""
    hit = cache_get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]
    value = await loader()
    cache_set(key, value)
    return value


def cache_stats() -> dict[str, int]:
    total = stats["hit"] + stats["miss"]
    return {
        **stats,
        "size": len(db_cache),
        "maxsize": db_cache.maxsize,
        "ttl": int(db_cache.ttl),
        "hit_rate": round(stats["hit"] / total, 3) if total else 0.0,
    }


# ---------------------------------------------------------------- 缓存 key 约定


def session_list_key() -> str:
    return "sessions:list"


def session_key(session_id: int) -> str:
    return f"session:{session_id}"


def session_messages_key(session_id: int) -> str:
    return f"session:{session_id}:messages"


def session_attachments_key(session_id: int) -> str:
    return f"session:{session_id}:attachments"


def session_traces_key(session_id: int) -> str:
    return f"session:{session_id}:traces"


def invalidate_session(session_id: int) -> None:
    """会话内容变化（新消息、新附件、改名、删除）时调用。"""
    invalidate_prefix(f"session:{session_id}")
    cache_delete(session_list_key())


def resource_list_key(slug: str) -> str:
    return f"resources:{slug}"


def resource_key(slug: str, item_id: int) -> str:
    return f"resource:{slug}:{item_id}"


def invalidate_resource(slug: str, item_id: int | None = None) -> None:
    # 注意：列表缓存的键带 q/limit/offset 后缀（见 resource_router.list_items），
    # 形如 resources:models:q=:l=100:o=0。只删裸键 resources:models 是删不掉的，
    # 会导致"页面保存成功但列表 60 秒内仍是旧数据"，必须按前缀清。
    invalidate_prefix(f"resources:{slug}")
    if item_id is not None:
        cache_delete(resource_key(slug, item_id))


SCENE_OVERVIEW_KEY = "scene:overview"
SCENE_CONFIG_KEY = "scene:config"
