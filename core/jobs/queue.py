"""core.jobs — 轻量内存任务队列（W4-3 队列解耦）。

把耗时任务（如大 PDF 解析、图片 OCR）从同步 HTTP 请求里拆出来：
接口立即返回 task_id，后台 worker 线程执行，调用方轮询状态取结果。
**进程内**（与 file_store 同生命周期），重启即清——与"上传文件是会话级临时用途"一致。

语义：
  - `TaskQueue.submit(fn, *args, **kwargs) -> task_id`：提交即返回，后台执行。
  - `TaskQueue.status(task_id) -> dict`：{status, error?, result?, created_at, finished_at?}
    status ∈ pending | processing | done | error。
  - **线程安全**（提交与 worker 并发更新任务表加锁）。
  - **worker 失败不崩**：异常捕获记到 task.error，状态置 error。

worker 数经 `Settings.job_queue_workers`（默认 2）配置；多实例部署时各实例各有一个
队列（任务在提交到的那个实例上执行）——与 W4-4 多副本的边界一致，长任务如需跨副本
持久化另议（当前单机够用）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"

#: 当前 worker 正在执行的任务 id（供任务回调读取自己的 task_id，如异步上传存 file_store）
_current_task_id: ContextVar[Optional[str]] = ContextVar("ztb_job_task_id", default=None)


@dataclass
class Task:
    id: str
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    status: str = PENDING
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


class TaskQueue:
    """轻量内存任务队列（后台 worker 线程）。"""

    def __init__(self, max_workers: int = 2) -> None:
        self._max_workers = max(1, int(max_workers))
        self._executor: Optional[ThreadPoolExecutor] = None
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix="ztb-job",
                    )
        return self._executor

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """提交任务，立即返回 task_id。"""
        task = Task(id=uuid.uuid4().hex, fn=fn, args=args, kwargs=kwargs)
        with self._lock:
            self._tasks[task.id] = task
        try:
            self._ensure_executor().submit(self._run, task)
        except Exception as exc:  # noqa: BLE001  提交失败 → 任务立即置 error
            self._finish(task, error=f"submit_failed: {type(exc).__name__}: {exc}")
        return task.id

    def _run(self, task: Task) -> None:
        with self._lock:
            task.status = PROCESSING
        token = _current_task_id.set(task.id)
        try:
            result = task.fn(*task.args, **task.kwargs)
        except Exception as exc:  # noqa: BLE001  任务失败 → error（不崩 worker）
            logger.warning("任务 %s 失败: %s", task.id[:8], exc)
            self._finish(task, error=f"{type(exc).__name__}: {exc}")
            return
        finally:
            _current_task_id.reset(token)
        self._finish(task, result=result)

    def _finish(self, task: Task, *, result: Any = None, error: Optional[str] = None) -> None:
        with self._lock:
            task.status = ERROR if error else DONE
            task.result = result
            task.error = error
            task.finished_at = time.time()

    def status(self, task_id: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return None
            return {
                "task_id": task_id,
                "status": t.status,
                "error": t.error,
                "done": t.status == DONE,
                "created_at": t.created_at,
                "finished_at": t.finished_at,
            }

    def result(self, task_id: str) -> Any:
        """任务完成结果（未完成/失败返回 None；失败时可用 status().error 查因）。"""
        with self._lock:
            t = self._tasks.get(task_id)
            return t.result if t and t.status == DONE else None

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status in (PENDING, PROCESSING))

    def shutdown(self) -> None:
        with self._lock:
            ex = self._executor
            self._executor = None
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)


#: 进程级单例
_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()


def get_queue() -> TaskQueue:
    """进程级任务队列单例（懒加载；worker 数取 Settings.job_queue_workers）。"""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                from core.config.settings import Settings

                _queue = TaskQueue(max_workers=Settings().job_queue_workers)
    return _queue


def reset_queue() -> None:
    """清空单例（测试用）。"""
    global _queue
    with _queue_lock:
        if _queue is not None:
            _queue.shutdown()
        _queue = None


__all__ = ["TaskQueue", "Task", "get_queue", "reset_queue", "_current_task_id",
           "PENDING", "PROCESSING", "DONE", "ERROR"]
