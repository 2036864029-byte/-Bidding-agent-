"""请求上下文（contextvars）— request_id / session_id / tenant_id 全链路透传（W2-2 / W4-3）。

背景（审计 D）：全仓无 request_id，一次请求在 API → graph → 节点 → LLM/检索的
日志无法串联。contextvars 在同步/异步、线程池（asyncio.to_thread 会复制上下文）
中均可用；线程池执行器（ThreadPoolExecutor）不复制上下文，故 `_execute_sql_with_timeout`
等跨线程处只保证不丢主线程已设值之外的语义（当前不跨池依赖 request_id）。

W4-3：新增 `tenant_id`——多租户身份，用于会话/检查点隔离与按租户配额。

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
_tenant_id: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)

#: 无租户时的默认标识（单租户/未传 X-Tenant-ID 时使用，保证向后兼容）
DEFAULT_TENANT = "default"

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


def set_tenant_id(value: Optional[str]) -> None:
    _tenant_id.set(value or DEFAULT_TENANT)


def get_tenant_id() -> str:
    """当前租户（未设置时返回 DEFAULT_TENANT，保证调用方拿到的总是非空）。"""
    return _tenant_id.get() or DEFAULT_TENANT


def tenant_scoped(session_id: str) -> str:
    """把会话 id 加上租户前缀 → 用作 thread_id（检查点/会话天然按租户隔离）。

    同一 session_id 在不同租户下互不可见（tenant A 无法读到 tenant B 的对话）。
    """
    return f"{get_tenant_id()}::{session_id}"


def reset_context() -> None:
    """清空上下文（测试用）。"""
    _request_id.set(None)
    _session_id.set(None)
    _api_key_id.set(None)
    _tenant_id.set(None)


__all__ = [
    "MISSING",
    "DEFAULT_TENANT",
    "new_request_id",
    "set_request_id",
    "get_request_id",
    "set_session_id",
    "get_session_id",
    "set_api_key_id",
    "get_api_key_id",
    "set_tenant_id",
    "get_tenant_id",
    "tenant_scoped",
    "reset_context",
]
