# 功能：FastAPI 依赖 — AgentGraph 进程级懒加载单例。
"""deps（方案 Phase 2 / C-4）。

- get_agent()：threading.Lock 保护的 AgentGraph 进程级懒加载单例。
- checkpointer 后端读 env CHECKPOINTER_BACKEND（缺省取 Settings，W2-4 起默认 sqlite）。

W2-4：SQLite 后端下检查点落盘、跨进程共享，多 worker 不再串会话；
同时把会话登记表的清理器接到 AgentGraph.delete_thread（TTL 到期删检查点）。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from agent import AgentGraph
from core.config.settings import Settings
from core.session import get_session_registry

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_agent: Optional[AgentGraph] = None


def get_agent() -> AgentGraph:
    """进程级 AgentGraph 单例（懒加载，线程安全）。"""
    global _agent
    if _agent is None:
        with _lock:
            if _agent is None:
                settings = Settings()
                backend = os.getenv(
                    "CHECKPOINTER_BACKEND", settings.checkpointer_backend,
                )
                _agent = AgentGraph(checkpointer_backend=backend)
                # W2-4：会话过期 → 删除其检查点
                get_session_registry().set_deleter(_agent.delete_thread)
                logger.info(
                    "api.deps: AgentGraph 就绪（checkpointer=%s，会话 TTL=%ds）",
                    backend, settings.session_ttl_seconds,
                )
    return _agent


def reset_agent() -> None:
    """清空单例（测试用）。"""
    global _agent
    _agent = None
