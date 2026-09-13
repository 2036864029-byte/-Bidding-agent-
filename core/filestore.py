# 功能：临时文件会话缓存（9.3 Track D / D-3；P6 结构收敛时由 api/ 下沉 core/）。
"""core.filestore — 上传文件解析产物的临时缓存（会话级，TTL 过期）。

**为什么在 core**：会话文件记录有三个跨层消费者——api 上传/状态端点、
agent.doc_context 的上下文渲染、`read_file` 工具（agent 层）。基础设施被
多层消费即应下沉，否则 agent 必须反向 import api。

后端可切换（与 C4/C5 同一模式）：
  - ``FILE_STORE_BACKEND=memory``（默认）：进程内 dict；
  - ``FILE_STORE_BACKEND=redis``：跨进程 / 跨副本共享。

**为什么需要 redis**：C7 起支持 ``API_WORKERS=N``，而进程内 dict 不跨进程共享
——worker A 上传的文件，请求落到 worker B 即 404（B1）。Redis 后端同时是
A3（会话文件自动附着）与 C1（临时文档索引）的存储层。

对外接口（put / get / list_by_session / new_file_id / reset_store）与后端无关；
两个后端语义一致：TTL 过期即"不存在"，``get`` 的 ``session_id`` 参数做归属校验。
"""

from __future__ import annotations

import logging
import pickle
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

FILE_TTL_SECONDS = 3600  # 1 小时过期

#: Redis key 前缀（记录 ``{prefix}:file:{id}`` / 会话索引 ``{prefix}:session:{sid}``）
_REDIS_PREFIX = "ztb:filestore"


@dataclass
class FileRecord:
    file_id: str
    source_name: str
    source_format: str
    markdown: str
    preview: str
    chunk_count: int
    session_id: str
    created_at: float


class _MemoryBackend:
    """进程内 dict 后端（默认；单 worker）。"""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._records: Dict[str, FileRecord] = {}
        self._sessions: Dict[str, List[str]] = {}

    def put(self, record: FileRecord) -> None:
        with self._lock:
            self._records[record.file_id] = record
            ids = self._sessions.setdefault(record.session_id, [])
            if record.file_id not in ids:
                ids.append(record.file_id)

    def get(self, file_id: str) -> Optional[FileRecord]:
        with self._lock:
            rec = self._records.get(file_id)
            if rec is None:
                return None
            if time.time() - rec.created_at > self._ttl:
                self._drop(file_id, rec.session_id)
                return None
            return rec

    def list_by_session(self, session_id: str) -> List[FileRecord]:
        with self._lock:
            ids = list(self._sessions.get(session_id, ()))
        out = [rec for rec in (self.get(fid) for fid in ids) if rec is not None]
        return sorted(out, key=lambda r: r.created_at, reverse=True)

    def _drop(self, file_id: str, session_id: str) -> None:
        """剔除单条记录（调用方持锁）。"""
        self._records.pop(file_id, None)
        ids = self._sessions.get(session_id)
        if ids and file_id in ids:
            ids.remove(file_id)
            if not ids:
                self._sessions.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._sessions.clear()


class _RedisBackend:
    """Redis 后端：多 worker / 多副本共享（B1）。

    - 记录 ``{prefix}:file:{file_id}`` → pickle(FileRecord)，TTL 由 ``SET`` 的 ex 保证；
    - 会话索引 ``{prefix}:session:{session_id}`` → SET(file_id)，``SADD`` 原子追加
      （避免读-改-写竞态），每次追加刷新索引 TTL；索引比单条记录活得久时，
      陈旧 id 由 ``get`` 返回 None 自然剔除，并顺手 ``SREM`` 清理。
    - Redis 故障：读按未命中、写跳过（记录丢失但服务不中断），日志留痕。
    """

    def __init__(self, ttl: int, url: str, client: Optional[object] = None) -> None:
        self._ttl = ttl
        if client is not None:                      # 测试注入（fakeredis）
            self._client = client
        else:
            import redis as _redis

            self._client = _redis.Redis.from_url(url, decode_responses=False)
            self._client.ping()                     # 探测失败 → _build_backend 降级内存

    def _rkey(self, file_id: str) -> str:
        return f"{_REDIS_PREFIX}:file:{file_id}"

    def _skey(self, session_id: str) -> str:
        return f"{_REDIS_PREFIX}:session:{session_id}"

    def put(self, record: FileRecord) -> None:
        try:
            pipe = self._client.pipeline()
            pipe.set(self._rkey(record.file_id), pickle.dumps(record), ex=self._ttl)
            pipe.sadd(self._skey(record.session_id), record.file_id)
            pipe.expire(self._skey(record.session_id), self._ttl)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001  Redis 故障不阻断上传
            logger.warning("file_store Redis 写入失败: %s", exc)

    def get(self, file_id: str) -> Optional[FileRecord]:
        try:
            raw = self._client.get(self._rkey(file_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_store Redis 读取失败: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return pickle.loads(raw)
        except Exception:  # noqa: BLE001  跨版本残留 → 删掉按不存在
            logger.warning("file_store 反序列化失败，删除脏 key: %s", file_id[:12])
            try:
                self._client.delete(self._rkey(file_id))
            except Exception:  # noqa: BLE001
                pass
            return None

    def list_by_session(self, session_id: str) -> List[FileRecord]:
        try:
            raw_ids = self._client.smembers(self._skey(session_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_store 会话索引读取失败: %s", exc)
            return []
        out: List[FileRecord] = []
        stale: List[str] = []
        for raw in raw_ids:
            fid = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            rec = self.get(fid)
            if rec is None:
                stale.append(fid)
            else:
                out.append(rec)
        if stale:
            try:
                self._client.srem(self._skey(session_id), *stale)
            except Exception:  # noqa: BLE001
                pass
        return sorted(out, key=lambda r: r.created_at, reverse=True)

    def clear(self) -> None:
        try:
            for rk in self._client.scan_iter(match=f"{_REDIS_PREFIX}:*"):
                self._client.delete(rk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_store Redis 清空失败: %s", exc)


def _build_backend():
    """按 Settings 构造后端；Redis 不可达则降级为内存后端（fail-open）。"""
    from core.config.settings import Settings

    s = Settings()
    ttl = int(getattr(s, "file_store_ttl_seconds", FILE_TTL_SECONDS) or FILE_TTL_SECONDS)
    backend = (getattr(s, "file_store_backend", "memory") or "memory").lower()
    if backend == "redis":
        url = getattr(s, "file_store_redis_url", "") or "redis://localhost:6379/0"
        try:
            return _RedisBackend(ttl, url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_store Redis 后端不可用（%s），降级为进程内后端", exc)
    return _MemoryBackend(ttl)


_backend = None
_backend_lock = threading.Lock()


def _get_backend():
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                _backend = _build_backend()
    return _backend


def new_file_id() -> str:
    return uuid.uuid4().hex


def put(record: FileRecord) -> None:
    _get_backend().put(record)


def get(file_id: str, *, session_id: Optional[str] = None) -> Optional[FileRecord]:
    """读取记录；过期/不存在返回 None。

    Args:
        file_id: 上传返回的文件 id。
        session_id: 传入时校验归属——非本会话的文件按"不存在"处理（避免
            跨会话把他人上传内容注入自己的 doc_qa 上下文，W0-6）。
    """
    rec = _get_backend().get(file_id)
    if rec is None:
        return None
    if session_id is not None and rec.session_id != session_id:
        return None
    return rec


def list_by_session(session_id: str) -> List[FileRecord]:
    """本会话未过期的文件记录（按上传时间倒序）。无则返回空列表。

    A3（会话文件自动附着）用；归属即由 session_id 本身保证。
    """
    return _get_backend().list_by_session(session_id)


def reset_store() -> None:
    """清空缓存并丢弃后端实例（测试 / 后端切换用）。"""
    global _backend
    with _backend_lock:
        if _backend is not None:
            _backend.clear()
        _backend = None
