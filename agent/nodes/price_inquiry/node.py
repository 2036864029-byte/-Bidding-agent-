"""询价节点 — 薄入口（Phase 6 / F-4 子图化）。

节点本体（node_price_inquiry + 三层守卫/引导/超时/POST_GUARD）已迁
`agent/nodes/price_inquiry/subgraph.py` 成为独立子图的内部节点（D-5 保留项
全部保留，见 subgraph.py 包 docstring）。本文件仅保留 `node_price_inquiry`
薄入口：主调 price 子图，config 转发 thread_id。

引擎（intent/queries/recall/...）经 price_engine 门面调用（absolute import，
见方案附录 D D-5）。
"""

from __future__ import annotations

from ...state import AgentState
from .subgraph import get_price_subgraph


def node_price_inquiry(state: AgentState, config=None) -> dict:
    """智能询价节点 — 薄入口：主调 price 子图（守卫/查询/渲染全在子图内）。

    子图共享通道 messages/business_result 自动合并；pg_* 独有键只在子图作用域内。
    config（thread_id）转发给子图 invoke，随父图 checkpointer 运行。
    """
    subgraph = get_price_subgraph()
    out = subgraph.invoke(dict(state), config=config)
    return {
        "business_result": out.get("business_result", {}),
        "messages": out.get("messages", []),
    }
