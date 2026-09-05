# 功能：临时文件会话缓存（9.3 Track D / D-3）。
"""api.file_store — 上传文件解析产物的进程内临时缓存。

TTL 过期；与 MemorySaver 相同限制：不跨进程共享，uvicorn 须 ``--workers 1``
（同 api/deps.py 文档化约束）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

FILE_TTL_SECONDS = 3600  # 1 小时过期


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


_lock = threading.Lock()
_store: Dict[str, FileRecord] = {}


def new_file_id() -> str:
    return uuid.uuid4().hex


def put(record: FileRecord) -> None:
    with _lock:
        _store[record.file_id] = record


def get(file_id: str) -> Optional[FileRecord]:
    """读取记录；过期自动清除。"""
    with _lock:
        rec = _store.get(file_id)
        if rec is None:
            return None
        if time.time() - rec.created_at > FILE_TTL_SECONDS:
            _store.pop(file_id, None)
            return None
        return rec


def reset_store() -> None:
    """清空缓存（测试用）。"""
    with _lock:
        _store.clear()
