# 功能：FastAPI 应用工厂 + /chat(invoke|SSE) + /health。
"""server（方案 Phase 2 / C-5）。

- create_app(get_agent=...) 工厂：便于测试注入 mock agent。
- POST /chat：stream=false → invoke 返回 ChatResponse；stream=true → SSE 逐 token 转发
  （W3-2：生成节点 .stream 实时写 sink → async 端点逐帧转发 token/result/error/done）。
- GET /health：agent 就绪标志。
- CORS origin 走 env CORS_ORIGINS（逗号分隔，默认 *）。
- 入口：python -m api.server（uvicorn，host/port 走 env，默认 127.0.0.1:8000）。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
import uuid
from functools import partial
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

import anyio
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi import File as FileParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent import AgentGraph
from core.config.settings import Settings
from core.errors.handlers import register_exception_handlers, unhandled_envelope
from core.observability.audit import record_audit
from core.observability.context import set_session_id
from core.observability.logging import setup_logging
from core.observability.metrics import content_type, render_metrics
from core.protocol.renderers.api import reply_to_response
from core.protocol.reply import to_reply
from core.session import get_session_registry
from core.streaming.sink import StreamSink, set_active_sink
from public_kb.ingestion.parse_tool import parse_file

from . import file_store
from .deps import get_agent
from .middleware import (
    ApiKeyAuthMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


def _resolve_session_id(session_id: Optional[str]) -> str:
    """会话 id：请求带则沿用，未带则生成随机 id（W0-6）。

    旧行为是 ``session_id or "default"``——所有匿名请求共享同一 thread，
    MemorySaver 按 thread 持久 → 对话历史互相串扰。改为随机 id 后匿名请求各自
    独立；服务端把生成的 id 随响应回传，客户端带回即可延续同一会话。
    """
    return session_id or uuid.uuid4().hex


def _resolve_context_docs(file_ids: list[str], session_id: str) -> list[tuple[str, str]]:
    """file_ids → (文件名, 清洗后 markdown) 列表；缺失/过期/非本会话抛 404。

    doc_qa 无检索模式（D-0-3）：文件上下文经 AgentGraph context_docs 注入。
    归属校验（W0-6）：只允许使用本会话上传的文件。
    """
    docs: list[tuple[str, str]] = []
    for fid in file_ids or []:
        rec = file_store.get(fid, session_id=session_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail=f"file_id 不存在、已过期或不属于本会话: {fid}",
            )
        docs.append((rec.source_name, rec.markdown))
    return docs


def _invoke_response(agent: AgentGraph, req: ChatRequest) -> ChatResponse:
    session_id = _resolve_session_id(req.session_id)
    set_session_id(session_id)  # W2-2：会话 id 进入日志上下文
    # E4：根 span 在中间件创建（此时 session 未知），这里给活跃的根 span 补会话关联
    from core.observability.tracing import session_attrs, tag_current_span

    tag_current_span(**session_attrs())
    get_session_registry().touch(session_id)  # W2-4：登记会话（触发惰性过期清理）
    t0 = time.perf_counter()
    result = agent.invoke(
        req.question,
        thread_id=session_id,
        context_docs=_resolve_context_docs(req.file_ids, session_id),
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    fallback_biz = {"branch": "fallback", "answer": "", "data": None}
    business_result = result.get("business_result") or fallback_biz
    reply = to_reply(
        business_result,
        intent=str(result.get("intent", "unknown")),
        latency_ms=latency_ms,
    )
    record_audit(
        action="chat",
        status="ok" if not reply.error_code else "error",
        session_id=session_id,
        question=req.question,
        latency_ms=latency_ms,
        error_code=reply.error_code,
    )
    return ChatResponse(**reply_to_response(reply), session_id=session_id)


def _run_stream_worker(
    agent: AgentGraph,
    req: ChatRequest,
    session_id: str,
    context_docs: list[tuple[str, str]],
    sink: StreamSink,
) -> None:
    """SSE 工作线程：完整跑一次 ``agent.invoke``，token 由生成节点实时写入 sink。

    W3-2：图执行是同步整链（节点契约 ``(state)->dict``），放在独立 daemon 线程；
    端点侧经 ``copy_context().run`` 显式带入上下文（本 Python 的 threading.Thread
    不自动拷贝 contextvars），生成节点的 ``emit_token`` 经 contextvar 命中 sink；
    端点侧从 sink 队列逐帧转发，互不阻塞。终态/异常也写 sink，供端点发
    ``result`` / ``error`` / ``done`` 事件。
    """
    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            req.question,
            thread_id=session_id,
            context_docs=context_docs,
        )
    except Exception:  # 图级异常（节点内异常已被 _with_fallback 兜住，到不了这里）
        logger.exception("SSE 流式图执行异常")
        record_audit(
            action="chat_stream", status="error", session_id=session_id,
            question=req.question, error_code="sys_2500",
        )
        sink.put_event("error", unhandled_envelope())
        sink.put_event("done", None)
        return

    latency_ms = int((time.perf_counter() - t0) * 1000)
    fallback_biz = {"branch": "fallback", "answer": "", "data": None}
    business_result = result.get("business_result") or fallback_biz
    reply = to_reply(
        business_result,
        intent=str(result.get("intent", "unknown")),
        latency_ms=latency_ms,
    )
    payload = reply_to_response(reply)
    payload["session_id"] = session_id
    record_audit(
        action="chat_stream",
        status="ok" if not reply.error_code else "error",
        session_id=session_id,
        question=req.question,
        latency_ms=latency_ms,
        error_code=reply.error_code,
    )
    sink.put_event("result", payload)
    sink.put_event("done", None)


async def _sse_stream(
    agent: AgentGraph,
    req: ChatRequest,
    session_id: str,
    context_docs: list[tuple[str, str]],
) -> AsyncIterator[str]:
    """SSE 事件流（async 生成器）：sink 队列 → ``token`` / ``result`` / ``error`` / ``done``。

    事件协议（W3-2）：
      - ``event: session``  —— 首个事件回传会话 id（请求未带时服务端生成）
      - ``event: token``    —— 生成节点增量 token（data={"delta": "..."}），客户端累积展示
      - ``event: result``   —— 终态结构化响应（与 POST /chat 同构 + session_id），
                              客户端以 ``result.answer`` 为权威终态（覆盖 token 累积）
      - ``event: error``    —— 图执行异常统一信封（不回内部细节）
      - ``event: done``     —— 流结束
    """
    sink = StreamSink()
    set_active_sink(sink)
    yield "event: session\ndata: " + json.dumps(
        {"session_id": session_id}, ensure_ascii=False
    ) + "\n\n"

    # threading.Thread 在此 Python 环境**不拷贝**调用方 contextvars（实测），
    # 而生成节点的 emit_token / 审计的 request_id 都依赖 contextvar —— 显式用
    # copy_context().run() 把当前上下文（含 sink、request_id、session_id）带入图线程。
    ctx = contextvars.copy_context()
    worker = threading.Thread(
        target=lambda: ctx.run(
            _run_stream_worker, agent, req, session_id, context_docs, sink,
        ),
        daemon=True,
        name=f"sse-{session_id[:8]}",
    )
    worker.start()

    while True:
        item = await anyio.to_thread.run_sync(sink.get)
        if item is None:  # 超时：无新事件；工作线程已退且无 done → 防悬挂收尾
            if not worker.is_alive():
                break
            continue
        kind, payload = item
        if kind == "token":
            yield "event: token\ndata: " + json.dumps(
                {"delta": payload}, ensure_ascii=False
            ) + "\n\n"
        elif kind == "result":
            yield "event: result\ndata: " + json.dumps(
                payload, ensure_ascii=False, default=str
            ) + "\n\n"
        elif kind == "error":
            yield "event: error\ndata: " + json.dumps(
                payload, ensure_ascii=False, default=str
            ) + "\n\n"
        elif kind == "done":
            break
    yield "event: done\ndata: {}\n\n"


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()]


# ── 上传闸门（W0-8）──────────────────────────────────────────
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _upload_limits() -> tuple[int, set[str]]:
    """返回 (最大字节数, 允许的扩展名集合)。"""
    settings = Settings()
    return (
        settings.max_upload_mb * 1024 * 1024,
        {ext.lower() for ext in settings.allowed_upload_exts},
    )


def _validate_upload_name(name: str, allowed_exts: set[str]) -> None:
    ext = Path(name).suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {ext or '(无扩展名)'}；允许：{sorted(allowed_exts)}",
        )


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """分块读取上传内容，超限即中断——避免整文件读入内存（审计 A7/D 类）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小上限 {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(
    get_agent_fn: Callable[[], AgentGraph] = get_agent,
) -> FastAPI:
    """应用工厂。

    Args:
        get_agent_fn: 返回 AgentGraph 的依赖函数；测试可注入 mock。
    """
    app = FastAPI(title="ztb_demo Agent API", version="0.1.0")
    register_exception_handlers(app)  # W2-3：统一错误信封 + 不泄露内部细节

    # 中间件顺序：后添加的在外层。目标（外→内）：CORS → RequestContext → RateLimit，
    # 这样 request_id 先就位（限流 429 信封与访问日志都能带上），CORS 仍能处理预检。
    settings = Settings()
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
    )
    app.add_middleware(
        ApiKeyAuthMiddleware,
        keys=[k for k in settings.api_keys.split(",") if k.strip()],
        exempt_paths=[
            p.strip() for p in settings.auth_exempt_paths.split(",") if p.strip()
        ],
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health(agent: AgentGraph = Depends(get_agent_fn)) -> dict:
        return {"status": "ok", "agent_ready": agent is not None}

    @app.get("/metrics")
    def metrics() -> Response:
        """Prometheus 文本格式指标（W2-2）。"""
        return Response(content=render_metrics(), media_type=content_type())

    @app.post("/files/upload")
    async def upload_file(
        file: UploadFile = FileParam(...),
        session_id: Optional[str] = None,
    ) -> dict:
        """上传临时文件：FileParseTool 解析为清洗后 Markdown，返回 file_id + 预览。

        只存临时会话缓存（api.file_store），**不写入任何向量库**（防污染，D-4）。
        未传 session_id 时服务端生成并回传，后续 /chat 带上同一 id 才能使用该文件。
        """
        session = _resolve_session_id(session_id)
        set_session_id(session)  # W2-2：会话 id 进入日志上下文
        get_session_registry().touch(session)  # W2-4：登记会话
        name = file.filename or "upload"
        max_bytes, allowed_exts = _upload_limits()
        _validate_upload_name(name, allowed_exts)
        data = await _read_upload_limited(file, max_bytes)
        try:
            # W0-11：解析是 CPU/IO 重活，放到工作线程，避免阻塞事件循环
            result = await anyio.to_thread.run_sync(
                partial(parse_file, data, source_name=name)
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        record = file_store.FileRecord(
            file_id=file_store.new_file_id(),
            source_name=result.source_name,
            source_format=result.source_format,
            markdown=result.markdown,
            preview=result.preview,
            chunk_count=result.chunk_count,
            session_id=session,
            created_at=time.time(),
        )
        file_store.put(record)
        logger.info(
            "upload: file_id=%s name=%s format=%s chunks=%d",
            record.file_id, record.source_name, record.source_format, record.chunk_count,
        )
        record_audit(
            action="upload", status="ok", session_id=session,
            extra={"file": record.source_name, "bytes": len(data)},
        )
        return {
            "file_id": record.file_id,
            "source_name": record.source_name,
            "source_format": record.source_format,
            "preview": record.preview,
            "chunk_count": record.chunk_count,
            "session_id": session,
        }

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, agent: AgentGraph = Depends(get_agent_fn)) -> ChatResponse:
        if req.stream:
            raise HTTPException(status_code=400, detail="stream=true 请用 POST /chat/stream（SSE）")
        return _invoke_response(agent, req)

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest, agent: AgentGraph = Depends(get_agent_fn)):
        if not req.stream:
            # 兼容：stream=false 打到 /chat/stream 时退化为普通响应
            return _invoke_response(agent, req)
        session_id = _resolve_session_id(req.session_id)
        set_session_id(session_id)  # W2-2：会话 id 进入日志上下文
        get_session_registry().touch(session_id)  # W2-4：登记会话
        # 文件校验放在响应开始之前，失败返回正常 HTTP 404 而不是流中 error 事件
        context_docs = _resolve_context_docs(req.file_ids, session_id)
        return StreamingResponse(
            _sse_stream(agent, req, session_id, context_docs),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    # W0-7：先配置根 logger，再让 uvicorn 不接管日志（log_config=None），
    # 保证应用侧 INFO 埋点（分支耗时 / SQL profile / 检索埋点）在服务态落盘。
    setup_logging()
    # E0-3：初始化 OTel 追踪（未配置 ENABLE_TRACING/密钥时 no-op）
    from core.observability.tracing import setup_tracing

    setup_tracing()
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
