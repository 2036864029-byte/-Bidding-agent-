"""
knowledge_qa 节点 — 薄入口，按开关分发新旧链路（Phase 1 / B-4，P6 / F-2 子图化）。

- USE_TOOL_KB=false（默认）：委托 legacy.run_legacy_node（旧 rag.query()，行为不变）。
- USE_TOOL_KB=true：主调 knowledge_qa 子图（subgraph.py，kb_search → 生成 → 护栏
  → 拒答），config 转发 thread_id。本文件不持有任何 RAG 引擎/全局单例。
"""

from __future__ import annotations

import logging

from ...state import AgentState
from core.config.settings import Settings
from .legacy import run_legacy_node
from .subgraph import get_knowledge_qa_subgraph

logger = logging.getLogger(__name__)

# 开关在模块加载时读一次（可被测试 monkeypatch 覆盖）
_TOOL_PATH_ENABLED = Settings().enable_kb_tool_path


def _tool_path_enabled() -> bool:
    return _TOOL_PATH_ENABLED


def _run_tool_node(state: AgentState, config=None) -> dict:
    """工具路径节点体：主调 knowledge_qa 子图（无 RAG 引擎全局单例）。

    子图共享通道 messages/business_result 自动合并；qa_* 独有键只在子图作用域内。
    config（thread_id）转发给子图 invoke，随父图 checkpointer 运行。
    """
    subgraph = get_knowledge_qa_subgraph()
    out = subgraph.invoke(dict(state), config=config)
    return {
        "business_result": out.get("business_result", {}),
        "messages": out.get("messages", []),
    }


def node_knowledge_qa(state: AgentState, config=None) -> dict:
    """知识问答节点：按 USE_TOOL_KB 开关分发新旧链路。"""
    if _tool_path_enabled():
        return _run_tool_node(state, config)
    return run_legacy_node(state)
