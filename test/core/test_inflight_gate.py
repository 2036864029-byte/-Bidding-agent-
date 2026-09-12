"""全局并发闸门（C1）单测 — 纯离线，不依赖 API 服务。"""

from __future__ import annotations

import threading
import time

import pytest

from core.concurrency import limits
from core.concurrency.limits import InflightGate, get_inflight_gate, reset_inflight_gate


def test_disabled_when_limit_zero():
    """limit <= 0 → 关闭：恒放行，不统计。"""
    gate = InflightGate(0)
    assert gate.enabled is False
    assert gate.acquire() is True
    assert gate.acquire() is True
    assert gate.inflight == 0


def test_limits_concurrency_and_release():
    """limit=2：第 3 次立即失败；释放后可再入。"""
    gate = InflightGate(2, wait_timeout=0.0)
    assert gate.acquire() is True
    assert gate.acquire() is True
    assert gate.inflight == 2
    assert gate.acquire() is False          # 打满 → 快速失败
    assert gate.inflight == 2               # 失败不占槽

    gate.release()
    assert gate.inflight == 1
    assert gate.acquire() is True
    assert gate.inflight == 2


def test_acquire_waits_then_times_out():
    """等待超时后返回 False（不无限排队）。"""
    gate = InflightGate(1, wait_timeout=0.0)
    assert gate.acquire() is True

    t0 = time.monotonic()
    assert gate.acquire(timeout=0.15) is False
    elapsed = time.monotonic() - t0
    assert 0.1 <= elapsed < 2.0             # 确实等了约 0.15s，而非立即返回


def test_acquire_succeeds_after_release_from_other_thread():
    """另一线程释放后，等待者应被唤醒。"""
    gate = InflightGate(1, wait_timeout=2.0)
    assert gate.acquire() is True

    def _release_later():
        time.sleep(0.1)
        gate.release()

    threading.Thread(target=_release_later, daemon=True).start()
    t0 = time.monotonic()
    assert gate.acquire() is True           # 被唤醒
    assert time.monotonic() - t0 < 1.5


def test_release_is_idempotent():
    """超释放不应放开超额并发（BoundedSemaphore 语义）。"""
    gate = InflightGate(1, wait_timeout=0.0)
    assert gate.acquire() is True
    gate.release()
    gate.release()                          # 多余释放 → 只告警
    assert gate.inflight == 0
    assert gate.acquire() is True
    assert gate.acquire() is False          # 容量仍是 1，没被超释放撑大


def test_concurrent_workers_never_exceed_limit():
    """40 个线程抢 4 个槽：同时在飞数恒 <= 4。"""
    gate = InflightGate(4, wait_timeout=5.0)
    peak = 0
    lock = threading.Lock()
    entered = 0

    def _worker():
        nonlocal peak, entered
        if not gate.acquire():
            return
        try:
            with lock:
                entered += 1
                peak = max(peak, gate.inflight)
            time.sleep(0.02)
        finally:
            gate.release()

    threads = [threading.Thread(target=_worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert entered == 40
    assert peak <= 4
    assert gate.inflight == 0


def test_singleton_reads_settings_and_resets(monkeypatch):
    """单例从 Settings 读取上限；reset 后可重读。"""
    monkeypatch.setenv("MAX_INFLIGHT_REQUESTS", "7")
    monkeypatch.setenv("INFLIGHT_WAIT_TIMEOUT_SECONDS", "1.5")
    reset_inflight_gate()
    try:
        gate = get_inflight_gate()
        assert gate.limit == 7
        assert gate.wait_timeout == pytest.approx(1.5)
        assert get_inflight_gate() is gate      # 单例
        reset_inflight_gate()
        assert get_inflight_gate() is not gate
    finally:
        reset_inflight_gate()


def test_snapshot_shape():
    gate = InflightGate(3, wait_timeout=2.0)
    assert gate.acquire() is True
    snap = gate.snapshot()
    assert snap == {
        "limit": 3, "inflight": 1, "wait_timeout": 2.0, "enabled": True,
    }
    gate.release()
