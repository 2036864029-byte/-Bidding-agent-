# 功能：CLI 文本渲染 — AgentReply → 人类可读块。
"""CLI 渲染器（方案 Phase 2 / C-2）。

分层约束：本模块位于 core/，**不得反向 import public_kb**（core 为基础设施层）。
因此引用的富文本块用自含的通用渲染（读取 AgentReply.citations 的
context_index/doc_name/chapter/chunk_index/text/metadata 键），不套
public_kb.guardrails 的 format_citations。C-6 若需领域富格式，由 api/cli
（入口层，允许依赖 public_kb）在其上再包装。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..reply import AgentReply


def render_citations(citations: List[Dict[str, Any]], max_text_chars: int = 200) -> str:
    """把结构化引用渲染为可读文本块（保留【来源N】标签）。"""
    if not citations:
        return ""
    lines: List[str] = [f"📚 引用来源（共 {len(citations)} 条）", ""]
    for c in citations:
        idx = c.get("context_index", "")
        doc = c.get("doc_name") or "未知文档"
        chapter = c.get("chapter") or ""
        chunk_index = c.get("chunk_index")
        text = str(c.get("text") or "")
        header = f"【来源{idx}】{doc}"
        if chapter:
            header += f"｜{chapter}"
        if chunk_index is not None and chunk_index != -1:
            header += f"（块 {chunk_index}）"
        lines.append(header)
        meta = c.get("metadata") or {}
        meta_bits: List[str] = []
        for key in ("page_number", "source_file", "source_url", "publish_date"):
            if meta.get(key):
                meta_bits.append(f"{key}: {meta[key]}")
        if c.get("chunk_id") is not None:
            meta_bits.append(f"chunk_id: {c['chunk_id']}")
        if c.get("chunk_uid"):
            meta_bits.append(f"chunk_uid: {c['chunk_uid']}")
        if meta_bits:
            lines.append("  " + "\n  ".join(meta_bits))
        snippet = text[:max_text_chars] + ("…" if len(text) > max_text_chars else "")
        lines.append(f"  原文: {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_reply(reply: AgentReply) -> str:
    """AgentReply → CLI 文本块。"""
    parts: List[str] = []
    if reply.answer:
        parts.append(reply.answer)
    if reply.reply_type == "kb_answer" and reply.citations:
        block = render_citations(reply.citations)
        if block:
            parts.append("\n" + block)
    elif reply.data:
        if "sources" in reply.data:
            parts.append(f"\n引用来源: {len(reply.data['sources'])} 条")
        elif "records" in reply.data:
            parts.append(f"\n查询记录: {len(reply.data.get('records', []))} 条")
    return "\n".join(parts)
