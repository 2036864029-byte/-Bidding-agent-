# 功能：MySQL 全局连接池 + Settings 单例访问（价格查询链路基础设施层）。
"""MySQL 连接池 — 由 agent/nodes/price_inquiry/db.py 上提迁移（Phase 0 / A-10）。

W0-10 重写要点（审计 A5）：
  - **上限**：连接数受 ``Settings.mysql_pool_max`` 约束；池满时等待归还，超时返回
    None（调用方按"数据库不可用"降级为空结果），不再无限增长。
  - **锁外验证**：`ping()` 是网络 IO，移出锁外执行，避免所有 DB 访问被一把锁串行化。
  - **空洞清理**：失效连接置 None 后由 ``_compact_pool_locked`` 压实并重映射占用索引。
  - **超时语义修正**：socket 读写超时改由 ``Settings.mysql_read_timeout`` 显式给出
    （SSDictCursor 流式读大表需要较长窗口）；单条 SQL 的业务超时由
    ``price_engine.recall._execute_sql_with_timeout`` 的线程池控制。旧实现用
    ``kwargs.setdefault`` 想覆盖已存在的 key，从未生效。

配置（.env）：MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_PORT / MYSQL_CLEAN_DB。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import pymysql

from core.config.settings import Settings

logger = logging.getLogger(__name__)

_pool_lock = threading.Lock()
_pool_cond = threading.Condition(_pool_lock)

_pool_connections: list[Optional[pymysql.Connection]] = []

_pool_in_use: set[int] = set()

_settings_cache: Optional[Settings] = None


def _get_settings() -> Settings:
    """获取 Settings 单例（读根 .env）。"""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = Settings()
    return _settings_cache


def _mysql_base_kwargs() -> dict[str, Any]:
    settings = _get_settings()
    host = settings.mysql_host
    user = settings.mysql_user
    if not host or not user:
        raise RuntimeError(
            "MySQL 连接参数缺失：请在 .env 配置 MYSQL_HOST / MYSQL_USER"
            "（原硬编码默认 192.168.10.120/iflytek 已随 P0 A-10 移除）。"
        )
    return {
        "host": host,
        "user": user,
        "password": settings.mysql_password,
        "port": settings.mysql_port,
        "charset": "utf8mb4",
        "connect_timeout": min(10, settings.sql_query_timeout),
        "read_timeout": settings.mysql_read_timeout,
        "write_timeout": settings.mysql_read_timeout,
    }


def _safe_close(conn: Optional[pymysql.Connection]) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _pool_max() -> int:
    return max(1, _get_settings().mysql_pool_max)


def _live_count_locked() -> int:
    return sum(1 for conn in _pool_connections if conn is not None)


def _compact_pool_locked() -> None:
    """压实池：去掉失效连接留下的 None 空洞，并重映射占用索引。

    调用方必须持有 ``_pool_lock``。
    """
    global _pool_connections, _pool_in_use
    if not any(conn is None for conn in _pool_connections):
        return
    remap: dict[int, int] = {}
    kept: list[Optional[pymysql.Connection]] = []
    for old_index, conn in enumerate(_pool_connections):
        if conn is None:
            continue
        remap[old_index] = len(kept)
        kept.append(conn)
    _pool_in_use = {remap[i] for i in _pool_in_use if i in remap}
    _pool_connections = kept


def _get_connection(database: str) -> Optional[pymysql.Connection]:
    """从连接池获取 MySQL 连接。

    优先复用空闲连接（锁内只挑索引，存活验证在锁外做网络 ping），无空闲且未达
    上限时创建新连接；池满则等待归还，超时返回 None。
    """
    deadline = time.monotonic() + float(_get_settings().sql_query_timeout)

    while True:
        candidate: Optional[int] = None
        with _pool_lock:
            _compact_pool_locked()
            for index in range(len(_pool_connections)):
                if index not in _pool_in_use:
                    _pool_in_use.add(index)
                    candidate = index
                    break
            if candidate is None and _live_count_locked() >= _pool_max():
                _pool_cond.wait(timeout=0.5)
                if time.monotonic() >= deadline:
                    logger.warning(
                        "MySQL 连接池已满（上限 %d），等待可用连接超时", _pool_max()
                    )
                    return None
                continue

        if candidate is None:
            # 未达上限 → 新建连接
            return _create_connection(database)

        conn = _pool_connections[candidate]
        if conn is None:  # 压实后被置空，重试
            with _pool_lock:
                _pool_in_use.discard(candidate)
            continue
        try:
            # 锁外验证存活（网络 IO 不得在锁内做）
            conn.ping(reconnect=False)
            conn.select_db(database)
            return conn
        except Exception:
            with _pool_lock:
                if candidate < len(_pool_connections) and _pool_connections[candidate] is conn:
                    _pool_connections[candidate] = None
                _pool_in_use.discard(candidate)
                _pool_cond.notify_all()
            _safe_close(conn)


def _create_connection(database: str) -> Optional[pymysql.Connection]:
    """新建连接并纳入池管理。"""
    try:
        kwargs = _mysql_base_kwargs()
        conn = pymysql.connect(**kwargs, database=database)
        # 会话级空闲超时：让服务端容忍长连接的静默期
        with conn.cursor() as cur:
            cur.execute("SET SESSION net_read_timeout=28800")
            cur.execute("SET SESSION net_write_timeout=28800")
            cur.execute("SET SESSION wait_timeout=28800")
    except Exception as e:
        logger.error("连接 %s 失败: %s", database, e)
        return None

    with _pool_lock:
        _compact_pool_locked()
        _pool_connections.append(conn)
        _pool_in_use.add(len(_pool_connections) - 1)
    return conn


def _release_connection(conn: Optional[pymysql.Connection]) -> None:
    """释放连接回池（唤醒等待者）。

    ⚠️ **归还前必须结束事务**：pymysql 默认 ``autocommit=False``，连接从第一次
    SELECT 起就持有 InnoDB REPEATABLE READ 快照。若不做 rollback，长连接归还后
    被复用，会一直读到**数据重建前**的旧视图 —— 实测：重建 company_unified 后
    端点仍返回已合并掉的 CCGP 行。池内连接全为只读查询（全仓无池内写），
    rollback 是安全的：结束读事务、丢弃未提交（本来就是只读，无可丢的写）。
    rollback 是网络 IO，放在锁外执行。
    """
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass
    with _pool_lock:
        for index, pooled in enumerate(_pool_connections):
            if pooled is conn:
                _pool_in_use.discard(index)
                _pool_cond.notify_all()
                return
    # 不在池中（异常情况或已被 discard），直接关闭
    _safe_close(conn)


def discard_connection(conn: Optional[pymysql.Connection]) -> None:
    """把连接标记为不可复用：从池中摘除并关闭（W0-2）。

    用于 SQL 超时场景——提交到执行池的任务超时后可能**仍在使用该连接**，若把它
    放回池里，下一个请求会取到"正被别的线程使用"的连接，导致 pymysql 协议错乱
    （审计 A2）。摘除后 `_release_connection` 找不到它，会再 close 一次（幂等）。
    """
    if conn is None:
        return
    with _pool_lock:
        for index, pooled in enumerate(_pool_connections):
            if pooled is conn:
                _pool_connections[index] = None
                _pool_in_use.discard(index)
                break
        _pool_cond.notify_all()
    _safe_close(conn)


# 兼容旧名（模块级 _CLEAN_DB 常量以 env 为准，db.py 原读取语义）
_CLEAN_DB = _get_settings().mysql_clean_db
