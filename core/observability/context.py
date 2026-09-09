"""请求上下文（contextvars）— request_id / session_id 全链路透传（W2-2）。

背景（审计 D）：全仓无 request_id，一次请求在 API → graph → 节点 → LLM/检索的
日志无法串联。contextvars 在同步/异步、线程池（asyncio.to_thread 会复制上下文）
中均可用；线程池执行器（ThreadPoolExecutor）不复制上下文，故 `_execute_sql_with_timeout`
等跨线程处只保证不丢主线程已设值之外的语义（当前不跨池依赖 request_id）。

用法：
    from core.observability.context import set_request_id, get_request_id
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_api_key_id: ContextVar[Optional[str]] = ContextVar("api_key_id", default=None)

MISSING = "-"


def new_request_id() -> str:
    """生成一个新的 request_id（32 位 hex）。"""
    return uuid.uuid4().hex


def set_request_id(value: Optional[str]) -> None:
    _request_id.set(value)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_session_id(value: Optional[str]) -> None:
    _session_id.set(value)


def get_session_id() -> Optional[str]:
    return _session_id.get()


def set_api_key_id(value: Optional[str]) -> None:
    _api_key_id.set(value)


def get_api_key_id() -> Optional[str]:
    return _api_key_id.get()


def reset_context() -> None:
    """清空上下文（测试用）。"""
    _request_id.set(None)
    _session_id.set(None)
    _api_key_id.set(None)


__all__ = [
    "MISSING",
    "new_request_id",
    "set_request_id",
    "get_request_id",
    "set_session_id",
    "get_session_id",
    "set_api_key_id",
    "get_api_key_id",
    "reset_context",
]
