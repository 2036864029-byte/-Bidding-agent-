"""knowledge_qa 子图 — 知识问答的**唯一实现**（W1-1 收口后）。

流程：kb_search（检索 + 三元组）→ prompt|llm 生成 → 引用护栏 R1~R7 → 失败有界重试
→ 拒答。`node.node_knowledge_qa`（single 模式）与 supervisor 主图都直接调本子图；
LCEL 旧链路（`PublicKnowledgeRAG.query`）不再是 agent 路径。

状态边界（langgraph 子图命名空间）：
  - 共享通道 messages / business_result 自动合并回父图（AgentState 不动）。
  - qa_* 独有键只在子图作用域内读写，不污染 AgentState。

运行时单例（W1-1 自 agent/nodes/knowledge_qa/chain.py 迁入）：settings / llm /
validator / prompt 均带锁惰性创建。
"""

from __future__ import annotations

import logging
import threading
from typing import Annotated, Any, Dict, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, StateGraph, add_messages

from core.config.settings import Settings
from core.contracts import KnowledgeBaseContractError
from core.llm.registry import create_llm_for_slot
from core.observability.metrics import record_guardrail_fail
from public_kb.guardrails.citations_validate import CitationValidator
from public_kb.tools import Chunk, RetrievalResult, kb_search

from .prompts import build_prompt, build_retry_prompt

logger = logging.getLogger(__name__)

_REFUSAL_ANSWER = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"

_KB_NOT_READY_ANSWER = "知识库尚未初始化，请先执行入库操作。"
_KB_NOT_READY_MESSAGE = "知识库尚未初始化，请联系管理员。"
_NO_QUESTION_ANSWER = "抱歉，没有收到您的问题，请重新输入。"
# 护栏失败重试上限（F-3）：首轮生成 + ≤1 次带反馈重生成，有界不无限循环。
KB_MAX_ATTEMPTS = 2

# ── 运行时单例（带锁惰性；W1-1 自 chain.py 迁入）─────────────────
_lock = threading.Lock()
_settings: Optional[Settings] = None
_llm: Optional[BaseChatModel] = None
_validator: Optional[CitationValidator] = None
_prompt: Optional[Any] = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        with _lock:
            if _llm is None:
                # 槽位 kb_generation（models.yaml：model 继承 env LLM_MODEL，temp=0.0）
                _llm = create_llm_for_slot("kb_generation", _get_settings())
    return _llm


def _get_validator() -> CitationValidator:
    global _validator
    if _validator is None:
        _validator = CitationValidator(_get_settings().citation_rules)
    return _validator


def _get_prompt():
    global _prompt
    if _prompt is None:
        s = _get_settings()
        _prompt = build_prompt(
            s.system_prompt,
            enable_inline_citations=s.enable_inline_citations,
        )
    return _prompt


def reset_runtime() -> None:
    """清空惰性单例（测试用）。"""
    global _llm, _validator, _prompt, _settings
    _llm = _validator = _prompt = _settings = None


def _chunk_to_source(chunk: Chunk) -> Dict[str, Any]:
    """chunk → legacy sources 视图（保持既有消费方字段契约）。"""
    content = chunk.content
    return {
        "doc": chunk.doc_name or "未知文档",
        "chapter": chunk.chapter or "未知章节",
        "chunk_index": chunk.chunk_index,
        "content_snippet": content[:200] + "..." if len(content) > 200 else content,
        "score": chunk.score,
    }


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
        qa_attempts: 已生成次数（子图独有；重试有界用）。
        qa_feedback: 护栏失败原因反馈（子图独有；注入下一轮 prompt 修改要求）。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    business_result: dict
    qa_question: str
    qa_retrieval: RetrievalResult
    qa_raw_answer: str
    qa_error: str
    qa_aborted: bool
    qa_attempts: int
    qa_feedback: str


def _retry_enabled() -> bool:
    return _get_settings().kb_guardrail_retry


def _feedback_text(report) -> str:
    """从校验报告提取失败规则 → 重试反馈文本（可读、可喂 prompt）。"""
    failed = [r for r in report.rules if not r.passed]
    return "；".join(f"{r.name}（{r.rule_id}）: {r.detail or '未通过'}" for r in failed)


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
    except KnowledgeBaseContractError as e:
        # B9：空白/非法问题由 validate_question 抛 ValueError 子类；旧实现只捕
        # RuntimeError，异常冒泡到图级 _with_fallback → 用户看到"功能暂不可用"。
        logger.warning("knowledge_qa(subgraph): 问题不合法 — %s", e)
        return {
            "qa_aborted": True,
            "business_result": {
                "branch": "knowledge_qa",
                "answer": _NO_QUESTION_ANSWER,
                "data": {"sources": []},
            },
        }
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
    """② 生成：prompt|槽位 kb_generation|parser（同 chain 现状；护栏失败后带反馈重生成）。"""
    retrieval = state["qa_retrieval"]
    feedback = state.get("qa_feedback", "")
    if feedback:
        settings = _get_settings()
        retry_prompt = build_retry_prompt(
            settings.system_prompt,
            enable_inline_citations=settings.enable_inline_citations,
        )
        answer_chain = retry_prompt | _get_llm() | StrOutputParser()
        raw_answer: str = answer_chain.invoke({
            "context": retrieval.rendered_context,
            "question": state["qa_question"],
            "feedback": feedback,
        })
    else:
        answer_chain = _get_prompt() | _get_llm() | StrOutputParser()
        raw_answer: str = answer_chain.invoke({
            "context": retrieval.rendered_context,
            "question": state["qa_question"],
        })
    return {"qa_raw_answer": raw_answer.strip()}


def _finalize(retrieval: RetrievalResult, answer: str, report) -> dict:
    """护栏通过 / 重试耗尽：写终态 business_result + messages。"""
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


def _guardrail_node(state: KnowledgeQaState) -> dict:
    """③ 护栏：R1~R7 校验。失败 → 有界重试（反馈注入重生成）；耗尽 → 现状语义返回。"""
    retrieval = state["qa_retrieval"]
    answer = state["qa_raw_answer"]
    validator = _get_validator()
    context_ids = [c.chunk_id for c in retrieval.chunks]
    report = validator.validate(retrieval.citations_meta, answer, context_ids)

    if report.all_passed:
        return _finalize(retrieval, answer, report)

    record_guardrail_fail("knowledge_qa")
    attempts = state.get("qa_attempts", 0)
    logger.info(
        "knowledge_qa(subgraph): 引用校验失败 上下文=%d块 引用=%d条 attempts=%d",
        len(context_ids), len(retrieval.citations_meta), attempts,
    )
    if _retry_enabled() and attempts < KB_MAX_ATTEMPTS - 1:
        logger.info("knowledge_qa(subgraph): 触发护栏失败重试（第 %d 次重生成）", attempts + 1)
        return {
            "qa_attempts": attempts + 1,
            "qa_feedback": _feedback_text(report),
        }
    return _finalize(retrieval, answer, report)


def _route_after_guardrail(state: KnowledgeQaState) -> str:
    """护栏节点之后：已写终态 business_result → END；否则（重试）→ 回 generate。"""
    return "end" if state.get("business_result") else "generate"


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
    graph.add_conditional_edges(
        "guardrail", _route_after_guardrail,
        {"generate": "generate", "end": END},
    )
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
