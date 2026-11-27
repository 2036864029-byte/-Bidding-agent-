"""core.feishu_binding — 网页会话 ↔ 飞书 open_id 的绑定存储（并行线 A）。

「已连接飞书」的判定依据：网页 session 绑定了某个飞书 open_id。
- OAuth 回调拿到 open_id → ``bind()``；
- 前端查询 ``is_bound()`` → 决定「情况1 / 情况2」文案；
- 推送器对每个任务事件 ``lookup()`` 拿 open_id。

key = ``ztb:feishu:bind:<session_id>``，value = open_id，TTL = ``FEISHU_BINDING_TTL_S``。
后端：memory（进程内，默认）| redis（多 worker / 多副本共享）——仿 ``core.filestore``。
session_id 用**原始**网页会话 id（前端查询与任务事件都带原始 id，与 ``tenant_scoped``
的租户命名空间无关——绑定是会话级身份，不是租户级资源）。

open_id 形如 ``ou_xxxxxxxx``，入 Redis / 日志前须白名单校验（防非法字符）。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

OPEN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DEFAULT_TTL_SECONDS = 86400  # 一天；被 Settings.feishu_binding_ttl_seconds 覆盖


def is_valid_open_id(open_id: str) -> bool:
    return bool(OPEN_ID_RE.match(open_id or ""))


# ── 后端 ──────────────────────────────────────────────────────────


class _MemoryBackend:
    """进程内绑定表（默认；单 worker）。"""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def set(self, session_id: str, open_id: str) -> None:
        with self._lock:
            self._data[session_id] = {"open_id": open_id, "ts": time.time()}

    def get(self, session_id: str) -> Optional[str]:
        with self._lock:
            rec = self._data.get(session_id)
            if rec is None:
                return None
            if time.time() - rec["ts"] > self._ttl:
                self._data.pop(session_id, None)
                return None
            return rec["open_id"]

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class _RedisBackend:
    """Redis 绑定表：多 worker / 多副本共享。TTL 由 ``SET`` 的 ex 保证。"""

    _PREFIX = "ztb:feishu:bind"

    def __init__(self, ttl: int, url: str, client: Optional[object] = None) -> None:
        self._ttl = ttl
        if client is not None:  # 测试注入（fakeredis）
            self._client = client
        else:
            import redis as _redis

            self._client = _redis.Redis.from_url(url, decode_responses=True)
            self._client.ping()  # 探测失败 → _build_backend 降级 memory

    def _key(self, session_id: str) -> str:
        return f"{self._PREFIX}:{session_id}"

    def set(self, session_id: str, open_id: str) -> None:
        try:
            self._client.set(self._key(session_id), open_id, ex=self._ttl)
        except Exception as exc:  # noqa: BLE001  Redis 故障不阻断绑定
            logger.warning("飞书绑定 Redis 写入失败: %s", exc)

    def get(self, session_id: str) -> Optional[str]:
        try:
            raw = self._client.get(self._key(session_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书绑定 Redis 读取失败: %s", exc)
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else raw

    def delete(self, session_id: str) -> None:
        try:
            self._client.delete(self._key(session_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书绑定 Redis 删除失败: %s", exc)

    def clear(self) -> None:
        try:
            for rk in self._client.scan_iter(match=f"{self._PREFIX}:*"):
                self._client.delete(rk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书绑定 Redis 清空失败: %s", exc)


def _build_backend(ttl: int, backend: str, url: str):
    if (backend or "memory").lower() == "redis":
        try:
            store = _RedisBackend(ttl, url)
            store.set("__probe__", "ok")
            if store.get("__probe__") is None:
                raise RuntimeError("Redis 探测未回读")
            store.delete("__probe__")
            return store
        except Exception as exc:  # noqa: BLE001
            logger.warning("飞书绑定 Redis 后端不可用（%s），降级为进程内后端", exc)
    return _MemoryBackend(ttl)


_backend: Optional[object] = None
_backend_lock = threading.Lock()


def _get_backend():
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                from core.config.settings import Settings

                s = Settings()
                url = (
                    os.getenv("FEISHU_BINDING_REDIS_URL", "")
                    or getattr(s, "cache_redis_url", "")
                    or "redis://localhost:6379/0"
                )
                _backend = _build_backend(
                    int(getattr(s, "feishu_binding_ttl_seconds", DEFAULT_TTL_SECONDS)),
                    getattr(s, "feishu_binding_backend", "memory"),
                    url,
                )
    return _backend


def bind(session_id: str, open_id: str) -> bool:
    """绑定网页会话 ↔ 飞书 open_id。open_id 非法则丢弃并返回 False。"""
    if not is_valid_open_id(open_id):
        logger.warning("飞书绑定：open_id 非法，拒绝绑定 session=%s", session_id[:12])
        return False
    _get_backend().set(session_id, open_id)
    logger.info("飞书绑定: session=%s → open_id=%s", session_id[:12], open_id[:8])
    return True


def lookup(session_id: str) -> Optional[str]:
    """查会话绑定的 open_id（无绑定 / 过期返回 None）。"""
    return _get_backend().get(session_id)


def is_bound(session_id: str) -> bool:
    return lookup(session_id) is not None


def unbind(session_id: str) -> None:
    _get_backend().delete(session_id)


def reset_bindings() -> None:
    """清空绑定表并重置后端（测试用）。"""
    global _backend
    with _backend_lock:
        if _backend is not None:
            _backend.clear()
        _backend = None


__all__ = [
    "bind", "lookup", "is_bound", "unbind", "reset_bindings",
    "is_valid_open_id", "DEFAULT_TTL_SECONDS",
]
