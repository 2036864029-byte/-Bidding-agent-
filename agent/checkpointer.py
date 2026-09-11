"""
Checkpointer 工厂 — 对话记忆持久化抽象层。

Demo 阶段使用 MemorySaver（进程内存，重启丢失）。
通过 backend 参数可平滑升级至 SQLite / PostgreSQL / Redis，
业务代码零改动。

W2-4：
  - SQLite 分支修正——`SqliteSaver.from_conn_string()` 在 langgraph-checkpoint-sqlite
    3.x 返回**上下文管理器**（Iterator[SqliteSaver]），直接返回会导致图拿到
    generator；改为自建 `sqlite3.Connection` 后 `SqliteSaver(conn)`。
  - 新增 `delete_thread()`：会话过期清理用（后端不支持时静默忽略）。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


def create_checkpointer(
    backend: str = "memory",
    *,
    connection_string: Optional[str] = None,
) -> BaseCheckpointSaver:
    """Checkpointer 工厂，按需切换后端。

    Args:
        backend: 后端类型，可选 "memory" | "sqlite" | "postgres" | "redis"
        connection_string: 数据库连接串 / SQLite 文件路径（memory 模式忽略）

    Returns:
        BaseCheckpointSaver 实例

    Examples:
        checkpointer = create_checkpointer("memory")
        checkpointer = create_checkpointer("sqlite", connection_string="DATA/checkpoints.db")
    """
    if backend == "memory":
        logger.info("Checkpointer: 使用 MemorySaver（进程内存）")
        return MemorySaver()

    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            raise NotImplementedError(
                "SqliteSaver 尚未安装，请执行: uv add langgraph-checkpoint-sqlite"
            )
        db_path = connection_string
        if not db_path:
            from core.config.settings import Settings

            db_path = Settings().checkpointer_sqlite_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：uvicorn 线程池中跨线程复用连接
        conn = sqlite3.connect(db_path, check_same_thread=False)
        logger.info("Checkpointer: 使用 SqliteSaver -> %s", db_path)
        return SqliteSaver(conn)

    if backend == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError:
            raise NotImplementedError(
                "PostgresSaver 尚未安装，请执行: pip install langgraph-checkpoint-postgres"
            )
        if not connection_string:
            raise ValueError("PostgreSQL 模式需要提供 connection_string")
        logger.info("Checkpointer: 使用 PostgresSaver")
        return PostgresSaver.from_conn_string(connection_string)

    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver
        except ImportError:
            raise NotImplementedError(
                "RedisSaver 尚未安装，请执行: pip install langgraph-checkpoint"
            )
        if not connection_string:
            connection_string = "redis://localhost:6379"
        logger.info("Checkpointer: 使用 RedisSaver -> %s", connection_string)
        return RedisSaver.from_conn_string(connection_string)

    raise ValueError(f"不支持的 checkpointer 后端: {backend}")


def delete_thread(checkpointer: BaseCheckpointSaver, thread_id: str) -> bool:
    """删除某 thread 的全部检查点（会话过期清理）。

    Returns:
        是否真正删除（后端不支持时返回 False，不抛错）。
    """
    deleter = getattr(checkpointer, "delete_thread", None)
    if not callable(deleter):
        return False
    try:
        deleter(thread_id)
        return True
    except Exception as exc:  # 清理失败不应影响主流程
        logger.warning("删除 thread 检查点失败 %s: %s", thread_id, exc)
        return False


def list_threads(checkpointer: BaseCheckpointSaver) -> dict:
    """枚举全部 thread → 最近检查点时间戳（epoch 秒）。

    从**检查点源头**读取，而非进程内会话登记表 → **多副本安全**
    （W4-4：任一副本都能看到全部 thread，不依赖是谁处理的）。
    """
    from datetime import datetime

    latest: dict = {}
    try:
        tuples = checkpointer.list(None)
    except Exception as exc:  # noqa: BLE001  后端不支持枚举时返回空
        logger.warning("枚举检查点线程失败: %s", exc)
        return {}
    for item in tuples:
        try:
            tid = item.config["configurable"]["thread_id"]
            ts_raw = item.checkpoint.get("ts")
            ts = datetime.fromisoformat(ts_raw).timestamp() if ts_raw else None
        except Exception:  # noqa: BLE001  单条异常跳过
            continue
        if ts is not None and ts > latest.get(tid, 0):
            latest[tid] = ts
    return latest


def sweep_expired_threads(
    checkpointer: BaseCheckpointSaver, ttl_seconds: int, *, now: Optional[float] = None,
) -> list:
    """删除超过 TTL 未活动的 thread（多副本安全的过期清理）。

    与进程内 `SessionRegistry` 的区别：本函数从检查点枚举线程，任何副本调用都能
    覆盖全部会话（含其他副本处理的），适合多副本部署下的定时清理。
    """
    import time

    if ttl_seconds <= 0:
        return []
    now = time.time() if now is None else now
    expired = [tid for tid, ts in list_threads(checkpointer).items()
               if now - ts > ttl_seconds]
    for tid in expired:
        delete_thread(checkpointer, tid)
    if expired:
        logger.info("检查点清理：删除 %d 个过期 thread", len(expired))
    return expired


__all__ = ["create_checkpointer", "delete_thread", "list_threads", "sweep_expired_threads"]
