"""Langfuse 客户端初始化失败后必须能恢复（D37）。

事故背景：`get_client()` 把"初始化失败"也写进 `_initialized`，于是一次网络抖动
（或换了 key 没重启）就会让整个进程生命周期内观测永久关闭 —— 对话照常，
但 Langfuse 一条 trace 都没有，且没有任何显式报错。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.obs import langfuse as obs


class FakeLangfuse:
    """第一次初始化抛错，之后成功。"""

    attempts = 0

    def __init__(self, **_kwargs: object) -> None:
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise RuntimeError("模拟首次连接失败")
        self.ok = True

    def auth_check(self) -> bool:
        return True


@pytest.fixture
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLangfuse.attempts = 0
    monkeypatch.setattr(obs, "_client", None)
    monkeypatch.setattr(obs, "_initialized", False)
    monkeypatch.setattr(obs, "_retry_after", 0.0)
    # settings 的 langfuse_enabled 是只读 property，整体替换成假的
    monkeypatch.setattr(
        obs,
        "settings",
        SimpleNamespace(
            langfuse_enabled=True,
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_url="http://langfuse.test",
            langfuse_public_url="http://langfuse.test",
        ),
    )
    monkeypatch.setattr("langfuse.Langfuse", FakeLangfuse)


def test_retries_after_initialization_failure(fresh_state: None) -> None:
    assert obs.get_client() is None, "第一次失败应降级"
    # 冷却期设为 0，模拟"下一次请求时已经过了冷却"
    obs._retry_after = 0.0
    client = obs.get_client()
    assert client is not None, "失败后必须还能重试，不能永久关闭观测"
    assert FakeLangfuse.attempts == 2


def test_caches_successful_client(fresh_state: None) -> None:
    FakeLangfuse.attempts = 1  # 让首次构造即成功（不会再抛错）
    first = obs.get_client()
    assert first is not None
    constructions = FakeLangfuse.attempts
    second = obs.get_client()
    assert first is second
    assert FakeLangfuse.attempts == constructions, "成功之后不该重复建客户端"


def test_warming_period_suppresses_repeated_attempts(fresh_state: None, monkeypatch) -> None:
    """冷却期内不要再打网络：否则每个请求都去 auth_check，等于 DDoS 自己。"""
    monkeypatch.setattr(obs, "FAILURE_COOLDOWN_S", 60.0)
    assert obs.get_client() is None
    assert obs.get_client() is None
    assert FakeLangfuse.attempts == 1, "冷却期内不应重复尝试初始化"


def test_client_status_exposes_failure_reason(fresh_state: None) -> None:
    """体检接口必须**真去看客户端**，只报"已配置"会在 SDK 缺失时骗人。

    实测事故：换了个没装 langfuse 的 Python 环境跑后端，整条链路在 Langfuse 上
    凭空消失，而 `/system/status` 一直显示 configured=true，白查半天。
    """
    broken = obs.client_status()
    assert broken["configured"] is True
    assert broken["ok"] is False, "初始化失败时 ok 必须是 False"
    assert "模拟首次连接失败" in broken["error"], "要把真实原因带出来"

    obs._retry_after = 0.0  # 过了冷却，第二次会成功
    healthy = obs.client_status()
    assert healthy["ok"] is True
    assert healthy["error"] == ""


def test_client_status_reports_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        obs, "settings", SimpleNamespace(langfuse_enabled=False, langfuse_public_url="")
    )
    state = obs.client_status()
    assert state["configured"] is False
    assert state["ok"] is False
    assert "LANGFUSE" in state["error"]


def test_propagated_does_not_swallow_body_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归：`with propagated(...)` 里抛出的异常必须**原样穿出去**。

    改造前代码是 `try: with propagate_attributes(...): yield`（整段包起来），于是业务体
    的异常也被那个 except 吞掉，然后第二次 `yield` —— contextlib 报
    `RuntimeError: generator didn't stop after throw()`，真正的错误被盖成一团噪音。
    实测踩过：数据库少一列（`Unknown column 'run_notes.pointer_scope'`）在日志里完全
    看不出来，最外层只剩"generator didn't stop after throw()"，排查方向直接跑偏。

    观测是旁路，绝不能改写主流程的异常。
    """
    import sys
    import types

    class FakeCM:
        def __init__(self) -> None:
            self.exited = False

        def __enter__(self) -> FakeCM:
            return self

        def __exit__(self, *_exc: object) -> bool:
            self.exited = True
            return False

    entered: list[FakeCM] = []
    fake_module = types.ModuleType("langfuse")
    fake_module.propagate_attributes = lambda **_kw: (  # type: ignore[attr-defined]
        entered.append(FakeCM()) or entered[-1]
    )
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)
    monkeypatch.setattr(obs, "get_client", lambda: object())

    with pytest.raises(ValueError, match="来自业务体"):
        with obs.propagated(session_id="session_1"):
            raise ValueError("来自业务体")

    assert entered and entered[0].exited, "传播上下文仍然要正常退出"


def test_propagated_degrades_when_sdk_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 里没有 `propagate_attributes` 时静默降级，不影响业务体跑完。"""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "langfuse", types.ModuleType("langfuse"))
    monkeypatch.setattr(obs, "get_client", lambda: object())

    ran = False
    with obs.propagated(session_id="session_1"):
        ran = True
    assert ran
