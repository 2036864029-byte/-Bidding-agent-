"""price_inquiry — 节点兼容门面（P3 引擎下沉后）。

node 本体（node_price_inquiry + 三层守卫/引导）保留于此；
引擎符号（intent/queries/recall/... 及连接池）re-export 自 price_engine。
`from agent.nodes.price_inquiry import X` 旧路径可用（L2）。
"""

from __future__ import annotations

import price_engine as _pe
from price_engine import *  # noqa: F401,F403  # 引擎 + 连接池符号
from .node import (
    _QUERY_INTENT_KEYWORDS,
    _SUB_ROUTE_MAP,
    _UNIFIED_GUIDANCE_TEXT,
    _build_capability_boundary_answer,
    _build_unified_guidance,
    _get_query_fn,
    _has_valid_query_entity,
    node_price_inquiry,
)

__all__ = list(_pe.__all__) + ['_QUERY_INTENT_KEYWORDS', '_SUB_ROUTE_MAP', '_UNIFIED_GUIDANCE_TEXT', '_build_capability_boundary_answer', '_build_unified_guidance', '_get_query_fn', '_has_valid_query_entity', 'node_price_inquiry']
