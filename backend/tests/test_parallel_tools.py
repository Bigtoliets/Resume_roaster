"""同一轮多工具并发执行（D36）。

模型是否真的"一轮里调两个工具"取决于它自己，这里只锁住平台的并发能力：
两个各 200ms 的工具并发跑，总耗时必须接近 200ms 而不是 400ms。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.agent.loop import _invoke_parallel
from app.db.models import Tool


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def get(self, _model: object, key: int) -> Tool:
        return Tool(id=key, name=f"tool{key}", display_name=f"工具{key}", impl_key="kb_search")


@pytest.fixture
def fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    # 签名要和真实的 execute_tool 一致（含 session_id / run_id），
    # 否则主循环多传一个参数就 TypeError
    async def fake_execute(  # noqa: ANN001
        db, *, tool, arguments, fallback_llm=None, session_id=None, run_id=None
    ):
        await asyncio.sleep(0.2)
        return {"ok": True, "summary": f"{tool.name} 完成"}

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: FakeSession())
    monkeypatch.setattr("app.agent.loop.execute_tool", fake_execute)


async def test_parallel_invocation_is_concurrent(fake_runtime: None) -> None:
    calls = [SimpleNamespace(id="c1", arguments={}), SimpleNamespace(id="c2", arguments={})]
    started = time.perf_counter()
    outcomes = await asyncio.gather(
        _invoke_parallel(calls[0], 1, None),  # type: ignore[arg-type]
        _invoke_parallel(calls[1], 2, None),  # type: ignore[arg-type]
    )
    elapsed = time.perf_counter() - started

    assert [outcome[2] for outcome in outcomes] == ["ok", "ok"]
    assert elapsed < 0.35, f"两个 200ms 的工具串行执行了：{elapsed:.2f}s"


async def test_parallel_invocation_isolates_sessions(fake_runtime: None) -> None:
    """并发任务必须各自开会话，否则 AsyncSession 会串连接。"""
    seen: list[int] = []

    class RecordingSession(FakeSession):
        async def get(self, _model: object, key: int) -> Tool:
            seen.append(id(self))
            return await super().get(_model, key)

    import app.db.session as session_module

    original = session_module.SessionLocal
    session_module.SessionLocal = lambda: RecordingSession()  # type: ignore[assignment]
    try:
        await asyncio.gather(
            _invoke_parallel(SimpleNamespace(id="c1", arguments={}), 1, None),  # type: ignore[arg-type]
            _invoke_parallel(SimpleNamespace(id="c2", arguments={}), 2, None),  # type: ignore[arg-type]
        )
    finally:
        session_module.SessionLocal = original  # type: ignore[assignment]

    assert len(set(seen)) == 2, "两个并发任务用了同一个会话"
