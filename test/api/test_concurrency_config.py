"""并发容量配置测试（第 2 批 C2/B6/B7/B8/B10）。

要点：**闸门与 anyio 同步端点线程池必须一起调**——线程池默认 40，闸门调大过它
就白调（请求过了闸门却排在线程池外）。这里锁住两者的关系与默认值。
"""

from __future__ import annotations

import pytest

#: anyio 同步端点线程池的出厂默认（用于测试后复原，避免污染其他用例）
_ANYIO_DEFAULT = 40


def test_apply_thread_limiter_sets_tokens_in_event_loop():
    """必须在事件循环内调用——否则 anyio 直接抛 NoEventLoopError。"""
    import anyio
    from anyio.to_thread import current_default_thread_limiter

    from api.server import _apply_thread_limiter

    async def main():
        _apply_thread_limiter(77)
        try:
            return current_default_thread_limiter().total_tokens
        finally:
            current_default_thread_limiter().total_tokens = _ANYIO_DEFAULT

    assert anyio.run(main) == 77


def test_lifespan_applies_configured_thread_limit(monkeypatch):
    """启动钩子把 Settings.chat_thread_limit 落到 anyio 线程池（C2）。"""
    import anyio
    from anyio.to_thread import current_default_thread_limiter

    from api.server import create_app

    monkeypatch.setenv("CHAT_THREAD_LIMIT", "66")
    app = create_app(get_agent_fn=lambda: None)

    async def main():
        async with app.router.lifespan_context(app):
            return current_default_thread_limiter().total_tokens

    try:
        assert anyio.run(main) == 66
    finally:
        async def restore():
            current_default_thread_limiter().total_tokens = _ANYIO_DEFAULT

        anyio.run(restore)


def test_thread_limit_not_below_gate():
    """不变量：线程池上限 ≥ 在飞闸门，否则闸门形同虚设。"""
    from core.config.settings import Settings

    s = Settings()
    if s.max_inflight_requests > 0 and s.chat_thread_limit > 0:
        assert s.chat_thread_limit >= s.max_inflight_requests


@pytest.mark.parametrize(
    "attr,env",
    [
        ("max_inflight_requests", "MAX_INFLIGHT_REQUESTS"),
        ("chat_thread_limit", "CHAT_THREAD_LIMIT"),
        ("query_pool_max_workers", "QUERY_POOL_MAX_WORKERS"),
        ("sql_pool_max_workers", "SQL_POOL_MAX_WORKERS"),
        ("mysql_pool_max", "MYSQL_POOL_MAX"),
    ],
)
def test_capacity_env_overrides_are_honored(monkeypatch, attr, env):
    """容量项都可经环境变量覆盖（部署时不必改代码）。"""
    monkeypatch.setenv(env, "7")
    assert getattr(__import__("core.config.settings", fromlist=["Settings"]).Settings(), attr) == 7


def test_raised_defaults_match_batch2_targets(monkeypatch):
    """B6/B10：默认值必须够 100 并发的目标，不能再停在旧的保守值。"""
    from core.config.settings import Settings

    for env in ("MAX_INFLIGHT_REQUESTS", "CHAT_THREAD_LIMIT",
                "QUERY_POOL_MAX_WORKERS", "SQL_POOL_MAX_WORKERS", "MYSQL_POOL_MAX"):
        monkeypatch.delenv(env, raising=False)
    s = Settings()

    assert s.max_inflight_requests == 128
    assert s.chat_thread_limit == 128
    assert s.query_pool_max_workers == 32
    assert s.sql_pool_max_workers == 32
    assert s.mysql_pool_max == 32


def test_cpu_pool_workers_default_raised(monkeypatch):
    """B8：解析进程池默认 2 → 8（5~10 并发上传不再长时间排队）。"""
    import importlib

    monkeypatch.delenv("CPU_POOL_WORKERS", raising=False)
    monkeypatch.delenv("CPU_POOL_ENABLED", raising=False)
    cpu_pool = importlib.import_module("core.jobs.cpu_pool")

    pool = None
    try:
        pool = cpu_pool._get_pool()
        assert pool._max_workers == 8
    finally:
        cpu_pool.reset_cpu_pool()
