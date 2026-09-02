# 功能：API 响应渲染 — AgentReply → ChatResponse dict。
"""API 渲染器（方案 Phase 2 / C-2）：把 AgentReply 映射为 /chat 响应结构。"""

from __future__ import annotations

from typing import Any, Dict

from ..reply import AgentReply


def reply_to_response(reply: AgentReply) -> Dict[str, Any]:
    """AgentReply → ChatResponse 字典（C-3 schemas 对应结构）。"""
    return {
        "answer": reply.answer,
        "reply_type": reply.reply_type,
        "intent": reply.trace.get("intent", "unknown"),
        "citations": reply.citations,
        "data": reply.data,
        "latency_ms": reply.trace.get("latency_ms"),
    }
