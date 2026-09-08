"""MySQL 连接池测试（Wave 0 / W0-10）。

审计 A5：旧实现持锁做网络 ping、无上限、失效连接留 None 空洞、`setdefault`
延长超时从未生效。本文件用假 pymysql 连接验证新语义。
"""

from __future__ import annotations

import pytest

import core.rdb.mysql_pool as pool


class _FakeCursorCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None


class FakeConn:
    def __init__(self, name: str = "conn", *, ping_error: Exception | None = None):
        self.name = name
        self.closed = False
        self.pinged = 0
        self.selected_db: str | None = None
        self._ping_error = ping_error

    def ping(self, reconnect: bool = False) -> None:
        self.pinged += 1
        if self._ping_error is not None:
            raise self._ping_error

    def select_db(self, database: str) -> None:
        self.selected_db = database

    def cursor(self):
        return _FakeCursorCtx()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate_pool(monkeypatch):
    """每个用例前后保存/恢复池状态与 Settings 缓存。"""
    saved_conns = list(pool._pool_connections)
    saved_in_use = set(pool._pool_in_use)
    saved_settings = pool._settings_cache
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_USER", "tester")
    monkeypatch.setenv("MYSQL_PASSWORD", "pwd")
    pool._settings_cache = None
    with pool._pool_lock:
        pool._pool_connections.clear()
        pool._pool_in_use.clear()
    yield
    with pool._pool_lock:
        pool._pool_connections[:] = saved_conns
        pool._pool_in_use.clear()
        pool._pool_in_use.update(saved_in_use)
    pool._settings_cache = saved_settings


def _patch_connect(monkeypatch, conns: list[FakeConn]):
    created: list[FakeConn] = []

    def fake_connect(**kwargs):
        conn = conns[len(created)] if len(created) < len(conns) else FakeConn("extra")
        created.append(conn)
        return conn

    monkeypatch.setattr(pool.pymysql, "connect", fake_connect)
    return created


def test_reuses_idle_connection(monkeypatch):
    conns = [FakeConn("a"), FakeConn("b")]
    created = _patch_connect(monkeypatch, conns)

    first = pool._get_connection("ztb_clean")
    pool._release_connection(first)
    second = pool._get_connection("ztb_clean")

    assert first is second
    assert len(created) == 1, "空闲连接应被复用而不是新建"
    assert first.selected_db == "ztb_clean"


def test_pool_full_waits_then_returns_none(monkeypatch):
    """池满且无归还时，等待超时后返回 None（不无限增长）。"""
    monkeypatch.setenv("MYSQL_POOL_MAX", "1")
    monkeypatch.setenv("SQL_QUERY_TIMEOUT", "1")
    pool._settings_cache = None
    _patch_connect(monkeypatch, [FakeConn("only")])

    held = pool._get_connection("ztb_clean")
    assert held is not None

    second = pool._get_connection("ztb_clean")

    assert second is None
    assert pool._live_count_locked() == 1


def test_dead_connection_is_dropped_and_replaced(monkeypatch):
    dead = FakeConn("dead", ping_error=RuntimeError("gone"))
    fresh = FakeConn("fresh")
    _patch_connect(monkeypatch, [dead, fresh])

    first = pool._get_connection("ztb_clean")
    pool._release_connection(first)

    second = pool._get_connection("ztb_clean")

    assert second is fresh
    assert dead.closed is True


def test_compact_pool_remaps_in_use_indices():
    keep = FakeConn("keep")
    with pool._pool_lock:
        pool._pool_connections.extend([None, keep, None])
        pool._pool_in_use.add(1)
        pool._compact_pool_locked()

        assert pool._pool_connections == [keep]
        assert pool._pool_in_use == {0}


def test_read_timeout_comes_from_settings(monkeypatch):
    """W0-10：socket 超时由 mysql_read_timeout 显式给出（旧 setdefault 是空操作）。"""
    monkeypatch.setenv("MYSQL_READ_TIMEOUT", "123")
    pool._settings_cache = None

    kwargs = pool._mysql_base_kwargs()

    assert kwargs["read_timeout"] == 123
    assert kwargs["write_timeout"] == 123
