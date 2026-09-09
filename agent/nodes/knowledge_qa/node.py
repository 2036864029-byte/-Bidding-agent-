"""knowledge_qa 节点 — 薄入口（W1-1：单一实现，恒走子图）。

历史（B-4/F-2）本节点按 `USE_TOOL_KB` 在 legacy `rag.query()` 与工具子图之间分发；
W1-1 收口后旧链路（legacy.py / chain.py）已删除，本节点只做一件事：把 state 交给
knowledge_qa 子图，并转发 config（thread_id）以复用父图 checkpointer。
"""

from __future__ import annotations

import logging

from ...state import AgentState
from .subgraph import get_knowledge_qa_subgraph

logger = logging.getLogger(__name__)


def node_knowledge_qa(state: AgentState, config=None) -> dict:
    """知识问答节点 — 薄入口：主调 knowledge_qa 子图（守卫/生成/护栏全在子图内）。

    子图共享通道 messages/business_result 自动合并；qa_* 独有键只在子图作用域内。
    config（thread_id）转发给子图 invoke，随父图 checkpointer 运行。
    """
    subgraph = get_knowledge_qa_subgraph()
    out = subgraph.invoke(dict(state), config=config)
    return {
        "business_result": out.get("business_result", {}),
        "messages": out.get("messages", []),
    }
