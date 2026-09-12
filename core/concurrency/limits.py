# 功能：进程级在飞请求闸门（C1）— 保护上游 LLM/检索，防并发打爆。
"""进程级在飞请求闸门。

背景：对话链路纯 IO 密集（embedding / rerank / LLM 全是远端 API），但上游
有严格速率上限（实测 SiliconFlow rerank 8 并发即 429）。此前**没有任何全局
并发控制** → 并发一高就同时打上游、自我 429。

与 `api/middleware/rate_limit.py` 的分工（互补，都要有）：
  - **限流**（rate_limit）：按**客户端**算速率（谁太快）→ 429；
  - **闸门**（本模块）：按**全局**算在飞数（服务器还能不能接）→ 503。

语义：
  - 进程级 `BoundedSemaphore`，限制「同时在飞」的业务请求数；
  - 获取带超时，超时即快速失败（不做无限排队——排队只会把延迟堆到超时）；
  - `limit <= 0` → 关闭（便于灰度/回退）。

多副本说明：本闸门是**进程级**的，N 个 worker 的总在飞上限 ≈ N × limit。
跨进程的总量控制在上 Redis 后由分布式限流承担（C4，Step ③）。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from core.config.settings import Settings

logger = logging.getLogger(__name__)


class InflightGate:
    """进程级在飞请求闸门（有界信号量 + 等待超时）。"""

    def __init__(self, limit: int, *, wait_timeout: float = 5.0) -> None:
        self._limit = max(0, int(limit))
        self._wait_timeout = max(0.0, float(wait_timeout))
        self._sem: Optional[threading.BoundedSemaphore] = (
            threading.BoundedSemaphore(self._limit) if self._limit > 0 else None
        )
        self._lock = threading.Lock()
        self._inflight = 0

    @property
    def enabled(self) -> bool:
        return self._sem is not None

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def wait_timeout(self) -> float:
        return self._wait_timeout

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def acquire(self, *, timeout: Optional[float] = None) -> bool:
        """占用一个槽位；超时返回 False（调用方应快速失败，不无限排队）。"""
        if self._sem is None:
            return True
        wait = self._wait_timeout if timeout is None else max(0.0, float(timeout))
        if wait <= 0:
            acquired = self._sem.acquire(blocking=False)
        else:
            acquired = self._sem.acquire(timeout=wait)
        if acquired:
            with self._lock:
                self._inflight += 1
        return acquired

    def release(self) -> None:
        """释放槽位（幂等：重复释放只告警，不抛错、不放开超额并发）。"""
        if self._sem is None:
            return
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1
        try:
            self._sem.release()
        except ValueError:  # BoundedSemaphore 超释放
            logger.warning("InflightGate 重复释放，已忽略")

    def snapshot(self) -> dict:
        """当前状态（供指标/日志）。"""
        return {
            "limit": self._limit,
            "inflight": self.inflight,
            "wait_timeout": self._wait_timeout,
            "enabled": self.enabled,
        }


_gate: Optional[InflightGate] = None
_gate_lock = threading.Lock()


def get_inflight_gate() -> InflightGate:
    """进程级单例（懒加载，配置来自 Settings）。"""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                settings = Settings()
                _gate = InflightGate(
                    settings.max_inflight_requests,
                    wait_timeout=settings.inflight_wait_timeout_seconds,
                )
                logger.info(
                    "INFLIGHT_GATE 初始化: limit=%s wait_timeout=%.1fs",
                    _gate.limit if _gate.enabled else "off",
                    _gate.wait_timeout,
                )
    return _gate


def reset_inflight_gate() -> None:
    """清空单例（测试用）。"""
    global _gate
    with _gate_lock:
        _gate = None


__all__ = ["InflightGate", "get_inflight_gate", "reset_inflight_gate"]
