"""
knowledge_qa 节点 — 薄入口，按开关分发新旧链路（Phase 1 / B-4）。

- USE_TOOL_KB=false（默认）：委托 legacy.run_legacy_node（旧 rag.query()，行为不变）。
- USE_TOOL_KB=true：走 chain.answer_knowledge（kb_search → prompt → 槽位
  kb_generation → 护栏 → 降级）。本文件不持有任何 RAG 引擎/全局单例。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from ...state import AgentState
from core.config.settings import Settings
from . import chain as _tool_chain
from .legacy import run_legacy_node

logger = logging.getLogger(__name__)

# 开关在模块加载时读一次（可被测试 monkeypatch 覆盖）
_TOOL_PATH_ENABLED = Settings().enable_kb_tool_path


def _tool_path_enabled() -> bool:
    return _TOOL_PATH_ENABLED


def _run_tool_node(state: AgentState) -> dict:
    """工具路径节点体（无 RAG 引擎全局单例）。"""
    messages = state.get("messages", [])
    if not messages:
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "抱歉，没有收到您的问题，请重新输入。",
                "data": {"sources": []},
            },
        }

    question = str(messages[-1].content)
    logger.info("knowledge_qa(tool): 处理问题 — %s", question[:80])

    try:
        result = _tool_chain.answer_knowledge(question)
        answer = result.get("answer", "抱歉，无法回答该问题。")
        logger.info("knowledge_qa(tool): 回答完成，引用 %d 条", len(result.get("citations", [])))
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": answer,
                "data": {
                    "sources": result.get("sources", []),
                    "citations": result.get("citations", []),
                    "citation_validation": result.get("citation_validation"),
                    "retrieval_diagnostics": result.get("retrieval_diagnostics"),
                },
            },
            "messages": [AIMessage(content=answer)],
        }
    except RuntimeError as e:
        # 知识库未初始化 / 检索不可用
        logger.warning("knowledge_qa(tool): 检索未就绪 — %s", e)
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "知识库尚未初始化，请先执行入库操作。",
                "data": {"sources": [], "error": str(e)},
            },
            "messages": [AIMessage(content="知识库尚未初始化，请联系管理员。")],
        }


def node_knowledge_qa(state: AgentState) -> dict:
    """知识问答节点：按 USE_TOOL_KB 开关分发新旧链路。"""
    if _tool_path_enabled():
        return _run_tool_node(state)
    return run_legacy_node(state)
