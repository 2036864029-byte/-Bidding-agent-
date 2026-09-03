"""knowledge_qa 子图（方案 Phase 6 / F-1）— 工具链路的图化编排。

把 chain.answer_knowledge 的线性流程（kb_search → prompt|llm → 护栏 → 拒答）改造成
可独立编译的 StateGraph 子图。F-2 起由 node.node_knowledge_qa 主调子图（config 转发
thread_id）；graph.py / NodeSpec / registry 零改动。

状态边界（langgraph 子图命名空间，已探针验证）：
  - 共享通道 messages / business_result 自动合并回父图（AgentState 不动）。
  - qa_* 独有键只在子图作用域内读写，不污染 AgentState —— 与 state.py 明示设计
    原则一致（P4-2 State 分层继续推迟）。

行为等价性（F-1 门禁）：与 chain.answer_knowledge / node._run_tool_node 在相同
mock fixtures 上逐键一致；重试循环属 F-3，本步不引入。
"""

from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, StateGraph, add_messages

from public_kb.tools import RetrievalResult, kb_search

from .chain import (
    _REFUSAL_ANSWER,
    _chunk_to_source,
    _get_llm,
    _get_prompt,
    _get_validator,
)

from core.observability.metrics import record_guardrail_fail

logger = logging.getLogger(__name__)

_KB_NOT_READY_ANSWER = "知识库尚未初始化，请先执行入库操作。"
_KB_NOT_READY_MESSAGE = "知识库尚未初始化，请联系管理员。"
_NO_QUESTION_ANSWER = "抱歉，没有收到您的问题，请重新输入。"


class KnowledgeQaState(TypedDict, total=False):
    """knowledge_qa 子图状态。共享通道合并回父图；qa_* 仅在子图作用域内。

    Attributes:
        messages: 对话历史（共享，add_messages reducer；子图读 question、写 AIMessage）。
        business_result: 业务负载（共享，终态节点写）。
        qa_question: 当前问题（子图独有）。
        qa_retrieval: kb_search 三元组（子图独有）。
        qa_raw_answer: LLM 生成回答（子图独有）。
        qa_error: 检索 RuntimeError 信息（子图独有；置位 → refuse_error 出口）。
        qa_aborted: 空消息守卫已写终态（子图独有；置位 → 直接 END）。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    business_result: dict
    qa_question: str
    qa_retrieval: RetrievalResult
    qa_raw_answer: str
    qa_error: str
    qa_aborted: bool


# ── 内部节点 ─────────────────────────────────────────────────


def _retrieve_node(state: KnowledgeQaState) -> dict:
    """① 检索：读问题 → kb_search → 三元组入子图状态（空消息守卫同 node 现状）。"""
    messages = state.get("messages", [])
    if not messages:
        return {
            "qa_aborted": True,
            "business_result": {
                "branch": "knowledge_qa",
                "answer": _NO_QUESTION_ANSWER,
                "data": {"sources": []},
            },
        }

    question = str(messages[-1].content)
    try:
        retrieval = kb_search(question)
    except RuntimeError as e:
        logger.warning("knowledge_qa(subgraph): 检索未就绪 — %s", e)
        return {"qa_error": str(e)}
    return {"qa_question": question, "qa_retrieval": retrieval}


def _route_after_retrieve(state: KnowledgeQaState) -> str:
    if state.get("qa_aborted"):
        return "end"
    if state.get("qa_error"):
        return "error"
    retrieval = state.get("qa_retrieval")
    if retrieval is None or not retrieval.chunks:
        return "refuse"
    return "generate"


def _generate_node(state: KnowledgeQaState) -> dict:
    """② 生成：prompt|槽位 kb_generation|parser（同 chain 现状）。"""
    retrieval = state["qa_retrieval"]
    answer_chain = _get_prompt() | _get_llm() | StrOutputParser()
    raw_answer: str = answer_chain.invoke({
        "context": retrieval.rendered_context,
        "question": state["qa_question"],
    })
    return {"qa_raw_answer": raw_answer.strip()}


def _guardrail_node(state: KnowledgeQaState) -> dict:
    """③ 护栏：R1~R7 校验 → 写终态 business_result + messages。"""
    retrieval = state["qa_retrieval"]
    answer = state["qa_raw_answer"]
    validator = _get_validator()
    context_ids = [c.chunk_id for c in retrieval.chunks]
    report = validator.validate(retrieval.citations_meta, answer, context_ids)
    if not report.all_passed:
        record_guardrail_fail("knowledge_qa")
    logger.info(
        "knowledge_qa(subgraph): 引用校验 上下文=%d块 引用=%d条 全部通过=%s",
        len(context_ids), len(retrieval.citations_meta), report.all_passed,
    )
    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": answer,
            "data": {
                "sources": [_chunk_to_source(c) for c in retrieval.chunks],
                "citations": [c.to_dict() for c in retrieval.citations_meta],
                "citation_validation": report.to_dict(),
                "retrieval_diagnostics": retrieval.recall_trace,
            },
        },
        "messages": [AIMessage(content=answer)],
    }


def _refuse_node(state: KnowledgeQaState) -> dict:
    """拒答：检索为空 → 护栏拒答报告 + 引导文案（同 chain 现状）。"""
    retrieval = state.get("qa_retrieval")
    trace = retrieval.recall_trace if retrieval is not None else {}
    validator = _get_validator()
    report = validator.validate([], _REFUSAL_ANSWER, [], is_refusal=True)
    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": _REFUSAL_ANSWER,
            "data": {
                "sources": [],
                "citations": [],
                "citation_validation": report.to_dict(),
                "retrieval_diagnostics": trace,
            },
        },
        "messages": [AIMessage(content=_REFUSAL_ANSWER)],
    }


def _refuse_error_node(state: KnowledgeQaState) -> dict:
    """检索未就绪（RuntimeError）→ 知识库未初始化引导（同 node 现状）。"""
    error = state.get("qa_error", "")
    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": _KB_NOT_READY_ANSWER,
            "data": {"sources": [], "error": error},
        },
        "messages": [AIMessage(content=_KB_NOT_READY_MESSAGE)],
    }


# ── 构建与单例 ────────────────────────────────────────────────


def build_knowledge_qa_subgraph():
    """构建并编译 knowledge_qa 子图（不带 checkpointer，随父图 thread_id 运行）。"""
    graph = StateGraph(KnowledgeQaState)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("generate", _generate_node)
    graph.add_node("guardrail", _guardrail_node)
    graph.add_node("refuse", _refuse_node)
    graph.add_node("refuse_error", _refuse_error_node)

    graph.set_entry_point("retrieve")
    graph.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        {
            "end": END,
            "error": "refuse_error",
            "refuse": "refuse",
            "generate": "generate",
        },
    )
    graph.add_edge("generate", "guardrail")
    graph.add_edge("guardrail", END)
    graph.add_edge("refuse", END)
    graph.add_edge("refuse_error", END)
    return graph.compile()


_subgraph = None


def get_knowledge_qa_subgraph():
    """进程级惰性子图单例（F-2 起由 node 主调）。"""
    global _subgraph
    if _subgraph is None:
        _subgraph = build_knowledge_qa_subgraph()
    return _subgraph


def reset_subgraph() -> None:
    """清空子图单例（测试/重载用）。"""
    global _subgraph
    _subgraph = None
