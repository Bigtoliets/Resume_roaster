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
