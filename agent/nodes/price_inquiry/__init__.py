"""price_inquiry — 节点兼容门面（P3 引擎下沉 + P6/F-4 子图化）。

node 本体（node_price_inquiry）为薄入口；三层守卫/引导/查询/渲染已迁
`subgraph.py` 独立子图（D-5 保留项全部保留）。本门面继续从 subgraph
re-export 守卫辅助函数与 node_price_inquiry，`from agent.nodes.price_inquiry import X`
旧路径可用（L2）；引擎符号（intent/queries/recall/... 及连接池）re-export 自 price_engine。
"""

from __future__ import annotations

import price_engine as _pe
from price_engine import *  # noqa: F401,F403  # 引擎 + 连接池符号
from .subgraph import (
    _QUERY_INTENT_KEYWORDS,
    _SUB_ROUTE_MAP,
    _UNIFIED_GUIDANCE_TEXT,
    _build_capability_boundary_answer,
    _build_unified_guidance,
    _get_query_fn,
    _has_valid_query_entity,
    get_price_subgraph,
)
from .node import node_price_inquiry

__all__ = list(_pe.__all__) + ['_QUERY_INTENT_KEYWORDS', '_SUB_ROUTE_MAP', '_UNIFIED_GUIDANCE_TEXT', '_build_capability_boundary_answer', '_build_unified_guidance', '_get_query_fn', '_has_valid_query_entity', 'get_price_subgraph', 'node_price_inquiry']
