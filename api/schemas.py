# 功能：/chat 请求/响应 Pydantic 模型。
"""API schemas（方案 Phase 2 / C-3）。

裁剪自方案一附录 9.1：当前无多租户/token 计量/用户体系实现，故去掉
tenant_id / token_usage / user_id（避免死字段）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.protocol.reply import ReplyType


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
    #: ⚠️ **从 `core.protocol.reply.ReplyType` 派生，不要在这里另抄一份字面量** ——
    #: 曾经抄了一份且漏了 `deep_agent`/`safety`，导致第 3 项上线后 agent 路由的问题
    #: **每个都 500**（pydantic 校验失败：`Input should be 'kb_answer', …`）。
    #: 派生后与出口协议天然同步，不会再漂。
    reply_type: ReplyType = "chat"
    intent: str = "unknown"
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    data: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    session_id: Optional[str] = Field(
        None,
        description="本轮实际使用的会话 id（请求未传时由服务端生成并回传，"
                    "客户端应原样带回以延续上下文 / 使用同一会话的上传文件）",
    )
    error_code: Optional[str] = Field(
        None,
        description="统一错误码（W2-3；正常回答为 null，见 core.errors.codes.ErrorCode）",
    )


class ReferenceRequest(BaseModel):
    """POST /inquiry/reference —— 项目概况相似检索与金额参考（方案 §6）。

    独立端点，不注册进 chat router（避开聊天路由的 LLM 分类）。
    """

    text: str = Field(
        "",
        max_length=5000,
        description="招标项目概况文本；与 file_ids 二选一或并用（纯文件上传时可为空，"
                    "有效性按「文件解析文本 + 文本」合并后判定）",
    )
    top_k: int = Field(10, ge=1, le=50, description="返回相似项目条数")
    min_amount_wan: Optional[float] = Field(
        None,
        ge=0,
        description="金额下限（万元）；只可上调，默认排除 ≤1000 元脏行",
    )
    region: Optional[str] = Field(
        None,
        description="可选地域过滤（省份全称，如 新疆维吾尔自治区）",
    )
    file_ids: List[str] = Field(
        default_factory=list,
        description="可选：/files/upload 返回的文件 id（招标文件/图片），"
                    "服务端把解析出的文本并入概况查询（与 text 同源复用解析链路）",
    )
    session_id: Optional[str] = Field(
        None,
        description="与 file_ids 配套：文件归属校验用的会话 id（不传则服务端生成，"
                    "此时只能访问该新会话名下刚上传的文件）",
    )
