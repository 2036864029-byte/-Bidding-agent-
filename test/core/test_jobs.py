"""core.jobs — 内存任务队列离线测试（W4-3 队列解耦）。"""

from __future__ import annotations

import time


def _queue(max_workers=2):
    from core.jobs import TaskQueue

    return TaskQueue(max_workers=max_workers)


def test_submit_and_poll_to_done():
    q = _queue()
    tid = q.submit(lambda x: x * 2, 21)
    # 轮询直到 done（worker 立即执行）
    for _ in range(100):
        st = q.status(tid)
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert st["status"] == "done"
    assert q.result(tid) == 42
    assert st["done"] is True and st["finished_at"] is not None


def test_task_error_captured():
    q = _queue()

    def _boom():
        raise RuntimeError("解析失败")

    tid = q.submit(_boom)
    for _ in range(100):
        st = q.status(tid)
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert st["status"] == "error"
    assert "RuntimeError" in st["error"]
    assert q.result(tid) is None


def test_status_unknown_returns_none():
    assert _queue().status("no-such-task") is None


def test_current_task_id_available_in_worker():
    """任务回调内能读到自己的 task_id（异步上传存 file_store 用）。"""
    from core.jobs import _current_task_id

    q = _queue()
    captured = {}

    def _worker():
        captured["tid"] = _current_task_id.get()
        return "ok"

    tid = q.submit(_worker)
    for _ in range(100):
        if q.status(tid)["status"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert captured["tid"] == tid


def test_pending_count_and_multiple_tasks():
    q = _queue(max_workers=1)

    def _slow():
        time.sleep(0.05)
        return 1

    t1, t2 = q.submit(_slow), q.submit(_slow)
    assert q.pending_count() >= 1     # 至少一个在排/执行
    for _ in range(200):
        if q.status(t1)["status"] in ("done", "error") and q.status(t2)["status"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert q.pending_count() == 0


def _poll(q, tid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = q.status(tid)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.01)
    raise AssertionError(f"任务未在 {timeout}s 内完成: {q.status(tid)}")


# ── B1 姊妹项：任务状态表跨 worker 共享 ──────────────────────


def _shared_stores():
    """两个 store 共用一个 fakeredis 实例（= 同一个部署的两个 worker）。"""
    import fakeredis

    from core.jobs.queue import _RedisStatusStore

    client = fakeredis.FakeRedis()
    return (
        _RedisStatusStore(ttl=60, url="", client=client),
        _RedisStatusStore(ttl=60, url="", client=client),
    )


def test_status_visible_from_other_worker():
    """worker A 提交 → worker B 也能查到终态与结果（原先多 worker 下返回 None → 404）。"""
    from core.jobs import TaskQueue

    store_a, store_b = _shared_stores()
    qa = TaskQueue(max_workers=1, status_store=store_a)
    qb = TaskQueue(max_workers=1, status_store=store_b)

    tid = qa.submit(lambda x: x * 2, 21)

    assert _poll(qa, tid)["status"] == "done"
    assert qb.status(tid)["status"] == "done", "跨 worker 查不到状态 = 前端轮询 404"
    assert qb.result(tid) == 42


def test_pending_task_visible_from_other_worker():
    """核心回归：任务**还在跑**时，另一个 worker 轮询必须拿到 pending/processing 而非 None。"""
    import threading

    from core.jobs import TaskQueue

    store_a, store_b = _shared_stores()
    qa = TaskQueue(max_workers=1, status_store=store_a)
    qb = TaskQueue(max_workers=1, status_store=store_b)

    gate = threading.Event()
    tid = qa.submit(gate.wait, 5)          # worker 线程阻塞在 gate 上
    try:
        st = qb.status(tid)
        assert st is not None, "在飞任务跨 worker 不可见（这正是 404 的成因）"
        assert st["status"] in ("pending", "processing")
    finally:
        gate.set()
        _poll(qa, tid)
        qa.shutdown()
        qb.shutdown()


def test_status_error_visible_across_workers():
    from core.jobs import TaskQueue

    store_a, store_b = _shared_stores()
    qa = TaskQueue(max_workers=1, status_store=store_a)
    qb = TaskQueue(max_workers=1, status_store=store_b)

    def _boom():
        raise ValueError("解析失败")

    tid = qa.submit(_boom)

    assert _poll(qa, tid)["status"] == "error"
    assert "ValueError" in qb.status(tid)["error"]
    assert qb.result(tid) is None


def test_non_json_result_keeps_status():
    """结果不可 JSON 化时只丢结果，状态照常可查（跨进程状态表不承载任意对象）。"""
    from core.jobs import TaskQueue
    from core.jobs.queue import _RedisStatusStore
    import fakeredis

    store = _RedisStatusStore(ttl=60, url="", client=fakeredis.FakeRedis())
    q = TaskQueue(max_workers=1, status_store=store)

    tid = q.submit(lambda: object())       # object() 不可 JSON 化

    assert _poll(q, tid)["status"] == "done"
    assert q.result(tid) is None


def test_memory_status_store_expires():
    from core.jobs.queue import _MemoryStatusStore

    store = _MemoryStatusStore(ttl=1)
    store.set("t", {"status": "done", "created_at": time.time() - 2})
    assert store.get("t") is None


def test_status_store_falls_back_when_redis_unreachable(monkeypatch):
    """Redis 挂掉不能让异步上传整体失效：降级进程内并留痕。"""
    from core.jobs.queue import _MemoryStatusStore, _build_status_store

    monkeypatch.setenv("CACHE_REDIS_URL", "redis://127.0.0.1:1/0")
    store = _build_status_store(ttl=60, backend="redis", url="redis://127.0.0.1:1/0")

    assert isinstance(store, _MemoryStatusStore)


def test_status_store_memory_by_default():
    from core.jobs.queue import _MemoryStatusStore, _build_status_store

    assert isinstance(_build_status_store(ttl=60, backend="memory", url=""), _MemoryStatusStore)


def test_finished_task_releases_closure():
    """完成后从在飞表移除——不再长期持有闭包捕获的上传字节。"""
    from core.jobs import TaskQueue

    q = TaskQueue(max_workers=1)
    tid = q.submit(lambda: "ok")
    _poll(q, tid)

    assert q.pending_count() == 0
    assert tid not in q._tasks
    assert q.status(tid) is not None      # 状态仍在（由状态表承载）
