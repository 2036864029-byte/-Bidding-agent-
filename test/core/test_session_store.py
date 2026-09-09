"""W2-4 会话登记与过期清理测试 + SQLite checkpointer 可用性。"""

from __future__ import annotations

from agent.checkpointer import create_checkpointer, delete_thread
from core.session.store import SessionRegistry

# ── SessionRegistry ──────────────────────────────────────────

def test_touch_creates_and_updates():
    registry = SessionRegistry(ttl_seconds=0)
    first = registry.touch("s1", now=100.0)
    assert first.turns == 0 and first.created_at == 100.0

    second = registry.touch("s1", now=110.0)
    assert second.turns == 1
    assert second.created_at == 100.0          # 创建时间不变
    assert second.last_seen == 110.0
    assert set(registry.snapshot()) == {"s1"}


def test_expired_and_cleanup_calls_deleter():
    registry = SessionRegistry(ttl_seconds=100, sweep_interval_seconds=10_000)
    deleted: list[str] = []
    registry.set_deleter(lambda sid: deleted.append(sid) or True)

    registry.touch("old", now=0.0)
    registry.touch("fresh", now=200.0)

    assert registry.expired(now=250.0) == ["old"]
    assert registry.cleanup(now=250.0) == 1
    assert deleted == ["old"]
    assert set(registry.snapshot()) == {"fresh"}


def test_cleanup_keeps_entry_when_deleter_fails():
    registry = SessionRegistry(ttl_seconds=10, sweep_interval_seconds=10_000)
    registry.set_deleter(lambda sid: False)
    registry.touch("s1", now=0.0)

    assert registry.cleanup(now=100.0) == 0
    assert "s1" in registry.snapshot(), "删除失败应保留登记，下轮重试"


def test_ttl_zero_disables_expiry():
    registry = SessionRegistry(ttl_seconds=0, sweep_interval_seconds=1)
    registry.touch("s1", now=0.0)
    assert registry.expired(now=10_000.0) == []
    assert registry.cleanup(now=10_000.0) == 0


def test_touch_triggers_lazy_sweep():
    """距上次扫描超过 sweep_interval 时，touch 会顺带清理过期会话。"""
    registry = SessionRegistry(ttl_seconds=100, sweep_interval_seconds=10)
    deleted: list[str] = []
    registry.set_deleter(lambda sid: deleted.append(sid) or True)

    registry.touch("stale", now=0.0)
    registry.touch("new", now=200.0)          # 触发扫描：stale 已过期

    assert deleted == ["stale"]
    assert set(registry.snapshot()) == {"new"}


# ── SQLite checkpointer ──────────────────────────────────────

def test_sqlite_checkpointer_is_usable_and_deletable(tmp_path):
    """W2-4：SqliteSaver 直接用连接构造（旧代码返回的是上下文管理器，不可用）。"""
    saver = create_checkpointer("sqlite", connection_string=str(tmp_path / "ckpt.db"))
    assert hasattr(saver, "get_tuple") and hasattr(saver, "put")
    assert delete_thread(saver, "thread-x") is True
