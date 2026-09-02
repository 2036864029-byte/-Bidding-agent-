"""MySQL 连接池（兼容壳）— 池已上提 core.rdb.mysql_pool（Phase 0 / A-10）。

保留此文件仅为兼容 price_inquiry 包内既有相对 import 与内部状态引用：

    from agent.nodes.price_inquiry.db import _get_connection, _release_connection, ...
    from agent.nodes.price_inquiry import _pool_connections, _pool_in_use  # 经包 __init__

新代码请直接使用：
    from core.rdb.mysql_pool import _get_connection, _release_connection, _CLEAN_DB

全部符号/内部状态对象均为 re-export（同一性保持），逻辑见 core/rdb/mysql_pool.py。
本兼容路径将在后续 Phase（P3 price_engine 下沉）移除。
"""

from __future__ import annotations

import warnings

# 池逻辑与内部状态（同一对象，供包 __init__ / 测试引用）
from core.rdb.mysql_pool import (  # noqa: F401
    _CLEAN_DB,
    _mysql_base_kwargs,
    _pool_connections,
    _pool_in_use,
    _pool_lock,
    _settings_cache,
)
from core.rdb.mysql_pool import _get_connection, _get_settings, _release_connection  # noqa: F401
from core.config.settings import Settings  # noqa: F401  # 兼容 db.Settings 属性引用

warnings.warn(
    "agent.nodes.price_inquiry.db 连接池已迁移至 core.rdb.mysql_pool，"
    "新代码请 `from core.rdb.mysql_pool import _get_connection`。"
    "本兼容路径将在 P3 price_engine 下沉时移除。",
    DeprecationWarning,
    stacklevel=2,
)
