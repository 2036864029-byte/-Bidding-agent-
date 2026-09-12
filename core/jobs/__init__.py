"""core.jobs — 轻量内存任务队列（W4-3 队列解耦）。"""

from __future__ import annotations

from .queue import (
    DONE,
    ERROR,
    PENDING,
    PROCESSING,
    Task,
    TaskQueue,
    _current_task_id,
    get_queue,
    reset_queue,
)

__all__ = [
    "TaskQueue", "Task", "get_queue", "reset_queue", "_current_task_id",
    "PENDING", "PROCESSING", "DONE", "ERROR",
]
