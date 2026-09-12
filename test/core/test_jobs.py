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
