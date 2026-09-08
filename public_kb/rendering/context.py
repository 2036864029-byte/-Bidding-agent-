# 功能：把检索到的 Document 格式化为 LLM 上下文和返回 sources。
"""Context and source formatting — moved from public_kb/generation/context.py (Phase 1 / B-2).

渲染依赖检索 Document 的 chunk 元数据契约（doc_name/chapter/chunk_index），
故留在 RAG 能力包内；generation/context.py 现为 re-export 兼容壳。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from langchain_core.documents import Document


def format_docs(docs_with_scores: List[Tuple[Document, float]]) -> str:
    """将检索到的文档列表格式化为拼接上下文字符串。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        带来源标注的拼接文本。优先使用父块拼接文本（metadata["context_text"]），
        无则回退子块原文（page_content）。
    """
    parts: List[str] = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        doc_name = doc.metadata.get("doc_name", "未知文档")
        chapter = doc.metadata.get("chapter", "未知章节")
        content = doc.metadata.get("context_text") or doc.page_content
        parts.append(
            f"[来源{i}] 文档: {doc_name} | 章节: {chapter} | 相关度: {score:.2%}\n"
            f"{content}"
        )
    return "\n\n---\n\n".join(parts)


def build_sources(
    docs_with_scores: List[Tuple[Document, float]],
) -> List[Dict[str, Any]]:
    """从检索结果构建引用来源列表（legacy 视图，保持向后兼容）。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        结构化来源信息列表。
    """
    return [
        {
            "doc": doc.metadata.get("doc_name", "未知文档"),
            "chapter": doc.metadata.get("chapter", "未知章节"),
            "chunk_index": doc.metadata.get("chunk_index", -1),
            "content_snippet": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            "score": round(score, 4),
        }
        for doc, score in docs_with_scores
    ]
