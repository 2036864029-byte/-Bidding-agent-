"""知识问答生成编排（工具路径，USE_TOOL_KB=true）— Phase 1 / B-4。

新链路：kb_search（public_kb.tools，返回三元组）→ 直接取 rendered_context /
citations_meta → models.yaml kb_generation 槽位 LLM 生成 → 护栏（R1~R7）校验
→ 拒答/回答。检索与渲染已由工具面完成，本模块只做"检索之后"的编排。

离线可测：chain.answer_knowledge 依赖的 kb_search / llm 均可注入/打桩。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser

from core.config.settings import Settings
from core.llm.registry import create_llm_for_slot
from public_kb.guardrails.citations_validate import CitationValidator
from public_kb.tools import Chunk, RetrievalResult, kb_search

from .prompts import build_prompt

logger = logging.getLogger(__name__)

_REFUSAL_ANSWER = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"

# ── 惰性单例（带锁，线程安全）──
_lock = threading.Lock()
_llm: Optional[BaseChatModel] = None
_validator: Optional[CitationValidator] = None
_prompt: Optional[Any] = None
_settings: Optional[Settings] = None


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


# ── 辅助：chunk → legacy sources 视图 ─────────────────────────

def _chunk_to_source(chunk: Chunk) -> Dict[str, Any]:
    content = chunk.content
    return {
        "doc": chunk.doc_name or "未知文档",
        "chapter": chunk.chapter or "未知章节",
        "chunk_index": chunk.chunk_index,
        "content_snippet": content[:200] + "..." if len(content) > 200 else content,
        "score": chunk.score,
    }


# ── 主入口 ─────────────────────────────────────────────────────

def answer_knowledge(question: str) -> Dict[str, Any]:
    """工具路径问答：检索 → 生成 → 护栏 → 返回（与旧 rag.query 同构结果契约）。

    Returns:
        {"answer", "sources", "citations", "citation_validation", "retrieval_diagnostics"}
    """
    retrieval: RetrievalResult = kb_search(question)
    chunks = retrieval.chunks
    trace = retrieval.recall_trace
    validator = _get_validator()

    # 检索为空 → 拒答（护栏生成拒答报告）
    if not chunks:
        logger.info("knowledge_qa(tool): 检索结果不足，触发拒答")
        report = validator.validate([], _REFUSAL_ANSWER, [], is_refusal=True)
        return {
            "answer": _REFUSAL_ANSWER,
            "sources": [],
            "citations": [],
            "citation_validation": report.to_dict(),
            "retrieval_diagnostics": trace,
        }

    citations = retrieval.citations_meta
    answer_chain = _get_prompt() | _get_llm() | StrOutputParser()
    raw_answer: str = answer_chain.invoke({
        "context": retrieval.rendered_context,
        "question": question,
    })
    answer = raw_answer.strip()

    context_ids = [c.chunk_id for c in chunks]
    report = validator.validate(citations, answer, context_ids)

    logger.info(
        "knowledge_qa(tool): 引用校验 上下文=%d块 引用=%d条 全部通过=%s",
        len(context_ids), len(citations), report.all_passed,
    )

    return {
        "answer": answer,
        "sources": [_chunk_to_source(c) for c in chunks],
        "citations": [c.to_dict() for c in citations],
        "citation_validation": report.to_dict(),
        "retrieval_diagnostics": trace,
    }
