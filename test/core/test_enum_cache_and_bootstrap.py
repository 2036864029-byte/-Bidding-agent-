"""枚举缓存污染与语义集合自举竞态测试（Wave 0 / W0-9）。

审计 B4：`_load_enum_values` 把"查询失败"也缓存成空列表 → 一次瞬时 DB 故障后
该列枚举归一本进程内永久失效。
审计 A4：自举触发是 check-then-set，无锁时并发可能启动两次重建，而重建第一步
是 drop_collection → 两个线程互删集合。
"""

from __future__ import annotations

import threading

import price_engine.enum_norm as enum_norm
import price_engine.semantic as semantic

# ── 枚举缓存 ─────────────────────────────────────────────────

class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return [("软件信息",), ("",), (None,)]


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        return None


def test_enum_cache_not_polluted_by_connection_failure(monkeypatch):
    """连不上库时不得写入缓存——否则故障恢复后仍永久失效。"""
    enum_norm._ENUM_CACHE.clear()
    monkeypatch.setattr(enum_norm, "_get_connection", lambda db: None)

    assert enum_norm._load_enum_values("company_info", "industry") == []
    assert ("company_info", "industry") not in enum_norm._ENUM_CACHE


def test_enum_cache_not_polluted_by_query_error(monkeypatch):
    class _BoomCursor(_FakeCursor):
        def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

    class _BoomConn(_FakeConn):
        def cursor(self):
            return _BoomCursor()

    enum_norm._ENUM_CACHE.clear()
    monkeypatch.setattr(enum_norm, "_get_connection", lambda db: _BoomConn())
    monkeypatch.setattr(enum_norm, "_release_connection", lambda conn: None)

    assert enum_norm._load_enum_values("company_info", "industry") == []
    assert ("company_info", "industry") not in enum_norm._ENUM_CACHE


def test_enum_cache_caches_successful_result(monkeypatch):
    """成功结果（含真空结果）照常缓存。"""
    enum_norm._ENUM_CACHE.clear()
    monkeypatch.setattr(enum_norm, "_get_connection", lambda db: _FakeConn())
    monkeypatch.setattr(enum_norm, "_release_connection", lambda conn: None)

    assert enum_norm._load_enum_values("company_info", "industry") == ["软件信息"]
    assert enum_norm._ENUM_CACHE[("company_info", "industry")] == ["软件信息"]
    enum_norm._ENUM_CACHE.clear()


# ── 自举触发去重 ─────────────────────────────────────────────

def test_bootstrap_thread_started_once(monkeypatch):
    """重复触发只允许启动一个重建线程（否则会互删集合）。"""
    done = threading.Event()
    started: list[int] = []

    def fake_rebuild():
        started.append(1)
        done.set()
        return True

    monkeypatch.setattr(semantic, "_SEMANTIC_BOOTSTRAP_IN_PROGRESS", False)
    monkeypatch.setattr(semantic, "_rebuild_mysql_semantic_collection", fake_rebuild)

    try:
        semantic._bootstrap_mysql_semantic_collection_async()
        semantic._bootstrap_mysql_semantic_collection_async()

        assert done.wait(2.0), "重建线程未启动"
        assert len(started) == 1
    finally:
        monkeypatch.setattr(semantic, "_SEMANTIC_BOOTSTRAP_IN_PROGRESS", False)
