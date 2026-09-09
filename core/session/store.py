"""会话元数据登记与过期清理（W2-4）。

背景（审计 D）：MemorySaver 常驻进程内存，任意 session_id 都会永久新增检查点
（内存只增不减）；切到 SQLite 后检查点落盘，同样需要 TTL 清理，否则库无限增长。

设计：
  - `SessionRegistry` 只登记元数据（session_id / 创建时间 / 最近访问 / 轮次），
    不持有消息本身（消息由 checkpointer 负责）。
  - 过期清理经注入的 `deleter`（`agent.checkpointer.delete_thread`）删除检查点。
  - 清理是**惰性**的：`touch()` 时若距上次扫描超过 `sweep_interval` 则扫一次，
    不额外起后台线程。
  - `ttl_seconds <= 0` 表示不过期（关闭清理）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class SessionMeta:
    """单个会话的元数据。"""

    session_id: str
    created_at: float
    last_seen: float
    turns: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "turns": self.turns,
        }


class SessionRegistry:
    """会话登记表（线程安全）。"""

    def __init__(
        self,
        ttl_seconds: int = 86400,
        sweep_interval_seconds: int = 300,
    ) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, SessionMeta] = {}
        self._ttl = int(ttl_seconds)
        self._sweep_interval = max(1, int(sweep_interval_seconds))
        self._last_sweep = 0.0
        self._deleter: Optional[Callable[[str], object]] = None

    # ── 注入清理器 ────────────────────────────────────────────
    def set_deleter(self, deleter: Optional[Callable[[str], object]]) -> None:
        """注入"删除某会话检查点"的回调（如 agent.checkpointer.delete_thread 的偏函数）。"""
        self._deleter = deleter

    # ── 登记 ──────────────────────────────────────────────────
    def touch(self, session_id: str, *, now: Optional[float] = None) -> SessionMeta:
        """登记一次会话访问（不存在则创建，存在则更新 last_seen / turns）。"""
        ts = now if now is not None else time.time()
        with self._lock:
            meta = self._sessions.get(session_id)
            if meta is None:
                meta = SessionMeta(session_id=session_id, created_at=ts, last_seen=ts)
                self._sessions[session_id] = meta
            else:
                meta.last_seen = ts
                meta.turns += 1
            should_sweep = self._ttl > 0 and (ts - self._last_sweep) >= self._sweep_interval
            if should_sweep:
                self._last_sweep = ts
        if should_sweep:
            self.cleanup(now=ts)
        return meta

    # ── 清理 ──────────────────────────────────────────────────
    def expired(self, *, now: Optional[float] = None) -> List[str]:
        """返回已过期会话 id 列表（ttl<=0 时恒为空）。"""
        if self._ttl <= 0:
            return []
        ts = now if now is not None else time.time()
        with self._lock:
            return [
                sid for sid, meta in self._sessions.items()
                if ts - meta.last_seen > self._ttl
            ]

    def cleanup(self, *, now: Optional[float] = None) -> int:
        """删除过期会话：调用 deleter 清检查点并移出登记表。返回清理条数。"""
        expired = self.expired(now=now)
        if not expired:
            return 0
        removed = 0
        for session_id in expired:
            deleted = True
            if self._deleter is not None:
                try:
                    deleted = bool(self._deleter(session_id))
                except Exception as exc:  # 清理失败保留登记，下轮再试
                    logger.warning("会话 %s 清理失败: %s", session_id, exc)
                    deleted = False
            if deleted:
                with self._lock:
                    self._sessions.pop(session_id, None)
                removed += 1
        if removed:
            logger.info("会话清理：移除 %d 个过期会话（ttl=%ds）", removed, self._ttl)
        return removed

    # ── 观测/测试 ─────────────────────────────────────────────
    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return {sid: meta.to_dict() for sid, meta in self._sessions.items()}

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._last_sweep = 0.0


_registry: Optional[SessionRegistry] = None
_registry_lock = threading.Lock()


def get_session_registry() -> SessionRegistry:
    """进程级会话登记表单例（参数取自 Settings）。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                settings = Settings()
                _registry = SessionRegistry(
                    ttl_seconds=settings.session_ttl_seconds,
                    sweep_interval_seconds=settings.session_sweep_interval_seconds,
                )
                logger.info(
                    "SessionRegistry 就绪：ttl=%ds sweep=%ds",
                    settings.session_ttl_seconds,
                    settings.session_sweep_interval_seconds,
                )
    return _registry


def reset_session_registry() -> None:
    """清空单例（测试用）。"""
    global _registry
    with _registry_lock:
        _registry = None


__all__ = [
    "SessionMeta",
    "SessionRegistry",
    "get_session_registry",
    "reset_session_registry",
]
