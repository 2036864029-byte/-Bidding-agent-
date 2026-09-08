"""线程池分池与 SQL 超时连接摘除测试（Wave 0 / W0-1、W0-2）。

审计 A1：price 链路曾把"整条查询"与"单条 SQL"提交进同一个 4-worker 池，
并发 ≥4 时 worker 全被外层占住并等内层 → 内层永不调度 → 全部超时。
审计 A2：SQL 超时后孤儿线程仍持有连接，调用方却把同一连接释放回池。
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError

import core.rdb.mysql_pool as mysql_pool
from core.concurrency.pools import get_query_pool, get_sql_pool, reset_pools

# ── W0-1：分池 ────────────────────────────────────────────────

def test_query_and_sql_pools_are_separate():
    """两池必须是不同实例——同池嵌套是并发悬崖的根因。"""
    reset_pools()
    try:
        assert get_query_pool() is not get_sql_pool()
    finally:
        reset_pools()


def test_pools_are_lazy_singletons():
    reset_pools()
    try:
        assert get_query_pool() is get_query_pool()
        assert get_sql_pool() is get_sql_pool()
    finally:
        reset_pools()


def test_pool_sizes_read_from_settings(monkeypatch):
    monkeypatch.setenv("QUERY_POOL_MAX_WORKERS", "3")
    monkeypatch.setenv("SQL_POOL_MAX_WORKERS", "5")
    reset_pools()
    try:
        assert get_query_pool()._max_workers == 3
        assert get_sql_pool()._max_workers == 5
    finally:
        reset_pools()


# ── W0-2：超时摘除连接 ────────────────────────────────────────

class _TimeoutFuture:
    def result(self, timeout=None):
        raise FutureTimeoutError()

    def cancel(self) -> bool:
        return True


class _TimeoutPool:
    def submit(self, fn, *args, **kwargs):
        return _TimeoutFuture()


def test_sql_timeout_discards_connection(monkeypatch):
    """超时后必须摘除该连接，否则会被下一个请求取走（协议错乱）。"""
    import price_engine.recall as recall

    discarded: list = []
    monkeypatch.setattr(recall, "get_sql_pool", lambda: _TimeoutPool())
    monkeypatch.setattr(recall, "discard_connection", lambda conn: discarded.append(conn))

    conn = object()
    rows, elapsed = recall._execute_sql_with_timeout(
        conn, "SELECT 1", (), "t", "stage", timeout=0.01
    )

    assert rows == []
    assert elapsed == 0.0
    assert discarded == [conn], "超时连接必须被标记为不可复用"


def test_discard_connection_removes_and_closes():
    """discard_connection 从池中摘除并关闭连接。"""

    class FakeConn:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    conn = FakeConn()
    saved_conns = list(mysql_pool._pool_connections)
    saved_in_use = set(mysql_pool._pool_in_use)
    try:
        with mysql_pool._pool_lock:
            mysql_pool._pool_connections.append(conn)
            idx = len(mysql_pool._pool_connections) - 1
            mysql_pool._pool_in_use.add(idx)

        mysql_pool.discard_connection(conn)

        assert conn.closed is True
        assert conn not in mysql_pool._pool_connections
        assert idx not in mysql_pool._pool_in_use
    finally:
        with mysql_pool._pool_lock:
            mysql_pool._pool_connections[:] = saved_conns
            mysql_pool._pool_in_use.clear()
            mysql_pool._pool_in_use.update(saved_in_use)
