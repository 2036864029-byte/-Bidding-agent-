# 功能：FastAPI 依赖 — AgentGraph 进程级懒加载单例。
"""deps（方案 Phase 2 / C-4）。

- get_agent()：threading.Lock 保护的 AgentGraph 进程级懒加载单例。
- checkpointer 后端读 env CHECKPOINTER_BACKEND（默认 memory）。

限制（文档化）：MemorySaver 不跨进程共享——uvicorn 须 ``--workers 1``。
多 worker 需先切 sqlite/postgres 后端（agent.checkpointer 已支持），否则每进程
独立记忆、会话串扰不可见。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from agent import AgentGraph

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_agent: Optional[AgentGraph] = None


def get_agent() -> AgentGraph:
    """进程级 AgentGraph 单例（懒加载，线程安全）。"""
    global _agent
    if _agent is None:
        with _lock:
            if _agent is None:
                backend = os.getenv("CHECKPOINTER_BACKEND", "memory")
                _agent = AgentGraph(checkpointer_backend=backend)
                logger.info("api.deps: AgentGraph 就绪（checkpointer=%s）", backend)
    return _agent


def reset_agent() -> None:
    """清空单例（测试用）。"""
    global _agent
    _agent = None
