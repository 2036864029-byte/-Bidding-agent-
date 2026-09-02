"""
knowledge_qa 旧路径实现（兼容壳，USE_TOOL_KB=false 时沿用）— Phase 1 / B-4。

旧节点直接调用现有 public_kb 的 RAG 引擎，保留其内部拒答逻辑与溯源引用。
B-4 后旧实现搬迁至此模块（含原 19-31 行全局单例），node.py 只做开关分发；
待 USE_TOOL_KB 切 true 并经 live 验证后，本模块与 rag.query 旧路径一并删除（P2）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from ...state import AgentState

logger = logging.getLogger(__name__)

# 全局单例，首次调用时惰性初始化（旧路径保留；新工具路径不持有 RAG 引擎）
_rag_engine: Any = None


def _get_rag():
    """惰性获取 PublicKnowledgeRAG 单例。"""
    global _rag_engine
    if _rag_engine is None:
        from public_kb import PublicKnowledgeRAG
        _rag_engine = PublicKnowledgeRAG()
        # 确保已加载已有集合（不重新入库）并构建问答链
        _rag_engine.ensure_loaded()
        logger.info("knowledge_qa(legacy): PublicKnowledgeRAG 初始化完成")
    return _rag_engine


def run_legacy_node(state: AgentState) -> dict:
    """旧路径节点体（与原 node_knowledge_qa 行为完全一致）。"""
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
    logger.info("knowledge_qa(legacy): 处理问题 — %s", question[:80])

    try:
        rag = _get_rag()
        result = rag.query(question)
        answer = result.get("answer", "抱歉，无法回答该问题。")
        sources = result.get("sources", [])
        citations = result.get("citations", [])
        citation_validation = result.get("citation_validation")

        logger.info(
            "knowledge_qa(legacy): 回答完成，引用来源 %d 条", len(sources)
        )

        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": answer,
                "data": {
                    "sources": sources,                 # legacy 视图
                    "citations": citations,             # 测评标准化引用
                    "citation_validation": citation_validation,  # 校验报告
                },
            },
            "messages": [AIMessage(content=answer)],
        }

    except RuntimeError as e:
        # 知识库未初始化
        logger.warning("knowledge_qa(legacy): RAG 未就绪 — %s", e)
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "知识库尚未初始化，请先执行入库操作。",
                "data": {"sources": [], "error": str(e)},
            },
            "messages": [AIMessage(content="⚠️ 知识库尚未初始化，请联系管理员。")],
        }
