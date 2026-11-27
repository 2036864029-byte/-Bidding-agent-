"""core.events — 极简进程内发布/订阅（任务进度事件用，并行线 A）。

为什么极简：进度推送只需要「业务点发事件 → 推送器订阅」这一种关系，
不需要 topic/优先级/重试。提供 subscribe / emit / clear_subscribers 三个原语。

线程安全：emit 可在任意线程被调（job worker / SSE 工作线程 / 请求线程）。
订阅者列表用锁保护，emit 时取快照调用。**订阅者必须快速返回**（或自行异步化）——
进度推送器正是这样：收到事件只做一次线程池提交，网络调用在池线程里进行。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List

logger = logging.getLogger(__name__)

_TOPIC = "feishu.progress"  # 单一主题，够用；将来多主题再加

_subscribers: List[Callable[[dict], None]] = []
_lock = threading.Lock()


def subscribe(fn: Callable[[dict], None]) -> None:
    """订阅进度事件；重复订阅同一 fn 幂等。"""
    with _lock:
        if fn not in _subscribers:
            _subscribers.append(fn)


def unsubscribe(fn: Callable[[dict], None]) -> None:
    with _lock:
        if fn in _subscribers:
            _subscribers.remove(fn)


def emit(payload: dict) -> None:
    """发布一条进度事件（同步调用订阅者快照；订阅者须快速返回）。

    payload 为业务语义 dict，约定含 ``kind`` 与 ``session_id``：
      upload_started / upload_succeeded / upload_failed / chat_result。
    """
    with _lock:
        snapshot = list(_subscribers)
    if not snapshot:
        return
    for fn in snapshot:
        try:
            fn(payload)
        except Exception:  # noqa: BLE001  订阅者异常不打断事件链
            logger.exception("事件订阅者异常 payload.kind=%s", payload.get("kind"))


def clear_subscribers() -> None:
    """清空订阅者（测试用）。"""
    with _lock:
        _subscribers.clear()


__all__ = ["subscribe", "unsubscribe", "emit", "clear_subscribers"]
