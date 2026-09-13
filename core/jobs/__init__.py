"""core.jobs — 轻量任务队列（W4-3 队列解耦；状态表可切 Redis，见 queue.py）。"""

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
