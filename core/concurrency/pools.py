# 功能：进程级线程池集中定义（Wave 0 / W0-1）。
"""线程池集中定义 —— "整条查询"与"单条 SQL"必须分池。

背景（审计 A1）：price 链路曾把"整条查询"（`agent/nodes/price_inquiry/subgraph.py`
的外层 submit）与"单条 SQL"（`price_engine/recall.py` 的内层 submit）提交进**同一个**
`max_workers=4` 的池。并发请求 ≥4 时，4 个 worker 全部被外层任务占住并在等内层
任务 → 内层任务永远排不到调度 → 每个请求逐阶段超时、整体返回空（有效并发上限≈3）。

约定：
  - ``get_query_pool()``：只跑"整条查询"（外层，price 子图用）。
  - ``get_sql_pool()``：只跑"单条 SQL"（内层，带超时执行）。
  - 任何一方都不得把任务提交进对方——否则重新引入同池嵌套。
  - 池大小经 ``Settings.query_pool_max_workers`` / ``sql_pool_max_workers`` 配置。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.config.settings import Settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_query_pool: Optional[ThreadPoolExecutor] = None
_sql_pool: Optional[ThreadPoolExecutor] = None


def get_query_pool() -> ThreadPoolExecutor:
    """整条查询执行池（外层）。"""
    global _query_pool
    if _query_pool is None:
        with _lock:
            if _query_pool is None:
                workers = Settings().query_pool_max_workers
                _query_pool = ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="price-query"
                )
                logger.info("QUERY_POOL 初始化: max_workers=%d", workers)
    return _query_pool


def get_sql_pool() -> ThreadPoolExecutor:
    """单条 SQL 执行池（内层，配合超时使用）。"""
    global _sql_pool
    if _sql_pool is None:
        with _lock:
            if _sql_pool is None:
                workers = Settings().sql_pool_max_workers
                _sql_pool = ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="sql-query"
                )
                logger.info("SQL_POOL 初始化: max_workers=%d", workers)
    return _sql_pool


def reset_pools() -> None:
    """清空池单例（测试用）。"""
    global _query_pool, _sql_pool
    with _lock:
        _query_pool = None
        _sql_pool = None
