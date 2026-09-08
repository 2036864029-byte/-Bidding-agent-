# 功能：/chat 请求/响应 Pydantic 模型。
"""API schemas（方案 Phase 2 / C-3）。

裁剪自方案一附录 9.1：当前无多租户/token 计量/用户体系实现，故去掉
tenant_id / token_usage / user_id（避免死字段）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题(1~2000字)")
    session_id: Optional[str] = Field(None, description="会话 id（映射 AgentGraph thread_id）")
    stream: bool = Field(False, description="true 时走 SSE 流式")
    file_ids: List[str] = Field(
        default_factory=list,
        description="POST /files/upload 返回的文件 id 列表（可选，doc_qa 消费）",
    )


class ChatResponse(BaseModel):
    answer: str
    reply_type: Literal["kb_answer", "price_records", "chat", "doc_qa", "fallback"] = "chat"
    intent: str = "unknown"
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    data: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    session_id: Optional[str] = Field(
        None,
        description="本轮实际使用的会话 id（请求未传时由服务端生成并回传，"
                    "客户端应原样带回以延续上下文 / 使用同一会话的上传文件）",
    )
