"""本地缓存：读穿透、写失效、前缀失效与统计。"""

from __future__ import annotations

import pytest

from app.core import cache


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    cache.clear_all()
    cache.stats.update({"hit": 0, "miss": 0, "invalidate": 0})


@pytest.mark.asyncio
async def test_cached_hits_after_first_load() -> None:
    calls = {"n": 0}

    async def loader() -> dict[str, int]:
        calls["n"] += 1
        return {"value": 42}

    first = await cache.cached("k", loader)
    second = await cache.cached("k", loader)
    assert first == second == {"value": 42}
    assert calls["n"] == 1, "第二次应命中缓存，不再调 loader"


@pytest.mark.asyncio
async def test_delete_forces_reload() -> None:
    calls = {"n": 0}

    async def loader() -> str:
        calls["n"] += 1
        return f"v{calls['n']}"

    assert await cache.cached("k", loader) == "v1"
    cache.cache_delete("k")
    assert await cache.cached("k", loader) == "v2"


def test_invalidate_prefix() -> None:
    cache.cache_set("session:1:messages", [1])
    cache.cache_set("session:1:traces", [2])
    cache.cache_set("session:2:messages", [3])
    removed = cache.invalidate_prefix("session:1")
    assert removed == 2
    assert cache.cache_get("session:2:messages") == [3]


def test_session_invalidate_also_drops_list() -> None:
    cache.cache_set(cache.session_list_key(), [{"id": 1}])
    cache.cache_set(cache.session_messages_key(1), ["m"])
    cache.invalidate_session(1)
    assert cache.cache_get(cache.session_messages_key(1)) is None
    assert cache.cache_get(cache.session_list_key()) is None


def test_stats_reports_hit_rate_and_limits() -> None:
    cache.cache_set("a", 1)
    cache.cache_get("a")
    cache.cache_get("missing")
    stats = cache.cache_stats()
    assert stats["hit"] == 1 and stats["miss"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["maxsize"] == 500
    assert stats["ttl"] == 60


def test_resource_keys_are_scoped() -> None:
    cache.cache_set(cache.resource_list_key("tools"), ["x"])
    cache.invalidate_resource("tools", 7)
    assert cache.cache_get(cache.resource_list_key("tools")) is None
