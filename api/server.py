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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

import anyio
from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi import File as FileParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent import AgentGraph, doc_context
from core import filestore
from core.concurrency.limits import InflightGate, get_inflight_gate
from core.config.settings import Settings
from core.errors.exceptions import ServiceBusyError
from core.errors.handlers import (
    error_envelope,
    register_exception_handlers,
    unhandled_envelope,
)
from core.observability.audit import record_audit
from core.observability.context import set_session_id, tenant_scoped
from core.observability.logging import setup_logging
from core.observability.metrics import content_type, render_metrics
from core.protocol.renderers.api import reply_to_response
from core.protocol.reply import to_reply
from core.session import get_session_registry
from core.streaming.sink import StreamSink, set_active_sink

from .deps import get_agent
from .middleware import (
    ApiKeyAuthMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from .modules import build_kanban_overview, build_supplier_overview
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


def _resolve_session_id(session_id: Optional[str]) -> str:
    """会话 id：请求带则沿用，未带则生成随机 id（W0-6）。

    旧行为是 ``session_id or "default"``——所有匿名请求共享同一 thread，
    MemorySaver 按 thread 持久 → 对话历史互相串扰。改为随机 id 后匿名请求各自
    独立；服务端把生成的 id 随响应回传，客户端带回即可延续同一会话。
    """
    return session_id or uuid.uuid4().hex


def _parse_upload(data: bytes, name: str) -> dict:
    """解析上传文件 → 可落库的 dict（**独立进程**执行）。

    解析是 CPU/GIL 密集：跑在主进程会与请求链路抢 GIL（实测 5 并发上传把同期
    提问 p95 从 3.41s 拉到 6.29s）。故交给 `core.jobs.cpu_pool` 在独立进程执行，
    主进程只负责把结果落进 filestore。

    **本函数是可替换接缝**：测试 monkeypatch 它注入假解析器，免起子进程。
    """
    from core.jobs.cpu_pool import parse_file_worker, run_cpu_task

    return run_cpu_task(parse_file_worker, data, name)


def _resolve_context_docs(
    file_ids: list[str], session_id: str, question: str
) -> list[tuple[str, str, str]]:
    """file_ids → (文件名, 上下文, "explicit")；缺失/过期/非本会话抛 404。

    上下文由 ``agent.doc_context`` 渲染（C1）：长文档走**文档内检索**（top-K 块），
    小文档 / 未建索引 / 检索失败回退全文——节点侧据块标记区分两种模式。
    归属校验（W0-6）：只允许使用本会话上传的文件。
    """
    records = []
    for fid in file_ids or []:
        rec = filestore.get(fid, session_id=session_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail=f"file_id 不存在、已过期或不属于本会话: {fid}",
            )
        records.append(rec)
    return doc_context.context_docs(records, question, kind="explicit")


def _session_context_docs(
    session_id: str, limit: int, question: str
) -> list[tuple[str, str, str]]:
    """A3：请求没带 file_ids 时，自动附着本会话最近 N 个未过期文件。

    条目带 ``"session"`` 标记 → 路由层按**弱信号**处理（L1 强规则优先），
    与客户端显式传 file_ids 的强信号区分开。N 经 `SESSION_FILE_AUTOLOAD_N` 配置，
    0 = 关闭。
    """
    if limit <= 0:
        return []
    recs = filestore.list_by_session(session_id)[:limit]
    if recs:
        logger.info("会话文件自动附着: session=%s files=%s", session_id[:12],
                    [r.source_name for r in recs])
    return doc_context.context_docs(recs, question, kind="session")


def _context_docs_for(req: ChatRequest, session_scope: str) -> list:
    """本轮要注入的文档上下文：显式 file_ids 优先，否则自动附着本会话文件。"""
    file_ids = [fid for fid in (req.file_ids or []) if fid]
    if file_ids:
        return _resolve_context_docs(file_ids, session_scope, req.question)
    return _session_context_docs(
        session_scope, Settings().session_file_autoload_n, req.question
    )


def _invoke_response(agent: AgentGraph, req: ChatRequest) -> ChatResponse:
    session_id = _resolve_session_id(req.session_id)  # 客户端可见的原始会话 id
    set_session_id(session_id)  # W2-2：会话 id 进入日志上下文
    session_scope = tenant_scoped(session_id)  # W4-3：内部隔离（检查点/会话/文件按租户命名空间）
    # E4：根 span 在中间件创建（此时 session 未知），这里给活跃的根 span 补会话关联
    from core.observability.tracing import session_attrs, tag_current_span

    tag_current_span(**session_attrs())
    get_session_registry().touch(session_scope)  # W2-4：登记会话（触发惰性过期清理）
    t0 = time.perf_counter()
    gate = get_inflight_gate()
    if not gate.acquire():
        logger.warning("在飞闸门打满，快速失败: limit=%d", gate.limit)
        raise ServiceBusyError(retry_after=max(1, int(gate.wait_timeout)))
    try:
        result = agent.invoke(
            req.question,
            thread_id=session_scope,
            context_docs=_context_docs_for(req, session_scope),
        )
    finally:
        gate.release()
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
    context_docs: list[tuple[str, str, str]],
    sink: StreamSink,
    gate: Optional[InflightGate] = None,
) -> None:
    """SSE 工作线程：完整跑一次 ``agent.invoke``，token 由生成节点实时写入 sink。

    W3-2：图执行是同步整链（节点契约 ``(state)->dict``），放在独立 daemon 线程；
    端点侧经 ``copy_context().run`` 显式带入上下文（本 Python 的 threading.Thread
    不自动拷贝 contextvars），生成节点的 ``emit_token`` 经 contextvar 命中 sink；
    端点侧从 sink 队列逐帧转发，互不阻塞。终态/异常也写 sink，供端点发
    ``result`` / ``error`` / ``done`` 事件。

    C1：``gate`` 由端点在建线程**前**取得，本函数负责在图执行完（含异常）后
    释放——否则过载时线程会先被创建再排队，等于没限住。
    """
    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            req.question,
            thread_id=tenant_scoped(session_id),  # W4-3：检查点按租户隔离
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
    finally:
        if gate is not None:
            gate.release()

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
    context_docs: list[tuple[str, str, str]],
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

    # C1/C3：**先取闸门再建线程**——否则 100 并发会先开出 100 个线程再排队，
    # 线程本身就把资源吃光了。取不到即快速失败（error + done 收尾）。
    gate = get_inflight_gate()
    if not gate.acquire():
        logger.warning("在飞闸门打满，SSE 快速失败: limit=%d", gate.limit)
        busy = ServiceBusyError(retry_after=max(1, int(gate.wait_timeout)))
        yield "event: error\ndata: " + json.dumps(
            error_envelope(busy), ensure_ascii=False
        ) + "\n\n"
        yield "event: done\ndata: {}\n\n"
        return

    # threading.Thread 在此 Python 环境**不拷贝**调用方 contextvars（实测），
    # 而生成节点的 emit_token / 审计的 request_id 都依赖 contextvar —— 显式用
    # copy_context().run() 把当前上下文（含 sink、request_id、session_id）带入图线程。
    ctx = contextvars.copy_context()
    worker = threading.Thread(
        target=lambda: ctx.run(
            _run_stream_worker, agent, req, session_id, context_docs, sink, gate,
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


def _apply_thread_limiter(limit: int) -> None:
    """C2/B7：把 anyio 同步端点线程池上限调到配置值。

    必须在**事件循环内**执行（见 `_lifespan`）——`current_default_thread_limiter()`
    在没有运行中的事件循环时会抛 NoEventLoopError。

    why：FastAPI 的同步端点（`/chat`）由 anyio 线程池代跑，**默认上限 40**。
    闸门（`max_inflight_requests`）一旦调大超过 40，线程池会先成为瓶颈：请求过了
    闸门却排在线程池外，看起来"容量没提升"。故两者必须一起调。
    """
    if limit <= 0:
        return
    try:
        from anyio.to_thread import current_default_thread_limiter

        limiter = current_default_thread_limiter()
        limiter.total_tokens = int(limit)
        logger.info("anyio 同步端点线程池上限 = %d", limiter.total_tokens)
    except Exception as exc:  # noqa: BLE001  版本/平台差异不应阻断启动
        logger.warning("anyio 线程池上限设置失败（沿用默认 40）: %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动钩子：应用级并发参数（必须在事件循环内生效）。"""
    _apply_thread_limiter(Settings().chat_thread_limit)
    yield


def create_app(
    get_agent_fn: Callable[[], AgentGraph] = get_agent,
) -> FastAPI:
    """应用工厂。

    Args:
        get_agent_fn: 返回 AgentGraph 的依赖函数；测试可注入 mock。
    """
    app = FastAPI(title="ztb_demo Agent API", version="0.1.0", lifespan=_lifespan)
    register_exception_handlers(app)  # W2-3：统一错误信封 + 不泄露内部细节

    # 中间件顺序：后添加的在外层。目标（外→内）：CORS → RequestContext → RateLimit，
    # 这样 request_id 先就位（限流 429 信封与访问日志都能带上），CORS 仍能处理预检。
    settings = Settings()
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
        global_per_minute=settings.rate_limit_global_per_minute,
        global_burst=settings.rate_limit_global_burst,
        # C4：redis 后端下多副本共享同一配额（memory 后端各副本各算一套）
        backend=settings.rate_limit_backend,
        redis_url=settings.cache_redis_url,   # 与检索缓存复用同一 Redis 实例
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

    @app.get("/kanban/overview")
    def kanban_overview(region: Optional[str] = None, industry: Optional[str] = None) -> dict:
        """商机看板聚合（读 `bidding` 库）。

        返回**原始形状**（snake_case），字段映射交前端 `kanbanAdapter`——
        与 `price_records` 的分层一致（见 docs/前端接口清单.md §3.2）。
        """
        return build_kanban_overview(settings, region=region, industry=industry)

    @app.get("/suppliers/overview")
    def suppliers_overview(
        province: Optional[str] = None,
        industry: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 60,
        offset: int = 0,
    ) -> dict:
        """供应商全景（读 `bidding.company_unified`）。

        按**地区 / 行业**筛选，返回基本信息 + 中标情况；默认按中标次数倒序分页。
        ⚠️ 工商字段只有 LEGACY 侧有，CCGP 侧那 1,058 家为 null，按地区/行业筛选不会出现。
        """
        return build_supplier_overview(
            settings, province=province, industry=industry, keyword=keyword,
            limit=limit, offset=offset,
        )

    @app.post("/files/upload")
    async def upload_file(
        file: UploadFile = FileParam(...),
        session_id: Optional[str] = None,
    ) -> dict:
        """上传临时文件：FileParseTool 解析为清洗后 Markdown，返回 file_id + 预览。

        只存临时会话缓存（core.filestore），**不写入任何向量库**（防污染，D-4）。
        未传 session_id 时服务端生成并回传，后续 /chat 带上同一 id 才能使用该文件。

        同步版本：小文件即时返回解析结果。大文件解析耗时，请用
        ``POST /files/upload/async`` + ``GET /files/{file_id}/status``（W4-3 队列解耦）。

        C1 起本端点**同步建文档索引**（仅长文档，见 agent/doc_context）：实测 60 块
        约 +3.7s（自建 bge-m3）。小文件不触发；长文档请优先走 async 端点，
        索引在那里同样是响应前建好（轮询到 done 即已就绪）。
        """
        session = _resolve_session_id(session_id)
        set_session_id(session)  # W2-2：会话 id 进入日志上下文
        session_scope = tenant_scoped(session)  # W4-3：文件按租户命名空间隔离
        get_session_registry().touch(session_scope)  # W2-4：登记会话
        name = file.filename or "upload"
        max_bytes, allowed_exts = _upload_limits()
        _validate_upload_name(name, allowed_exts)
        data = await _read_upload_limited(file, max_bytes)
        try:
            # W0-11 + 解析隔离：先放**工作线程**避免阻塞事件循环，再放**独立进程**
            # 避免解析抢主进程 GIL（见 _parse_upload / core.jobs.cpu_pool）
            res = await anyio.to_thread.run_sync(_parse_upload, data, name)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        record = filestore.FileRecord(
            file_id=filestore.new_file_id(),
            source_name=res["source_name"],
            source_format=res["source_format"],
            markdown=res["markdown"],
            preview=res["preview"],
            chunk_count=res["chunk_count"],
            session_id=session_scope,
            created_at=time.time(),
        )
        filestore.put(record)
        # C1：长文档顺带建**会话私有**文档索引（只进 Redis，不入公共 Milvus），
        # 提问时走文档内检索；小文档 / 失败即跳过（回退全文模式，见 agent/doc_context）
        t_index = time.perf_counter()
        doc_context.build_index_for(record)
        logger.info(
            "upload: file_id=%s name=%s format=%s chunks=%d index=%.2fs",
            record.file_id, record.source_name, record.source_format,
            record.chunk_count, time.perf_counter() - t_index,
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

    @app.post("/files/upload/async")
    async def upload_file_async(
        file: UploadFile = FileParam(...),
        session_id: Optional[str] = None,
    ) -> dict:
        """异步上传（W4-3 队列解耦）：立即返回 file_id + status=pending，后台解析。

        大文件（PDF/图片 OCR）解析耗时，用本端点避免 HTTP 阻塞；随后轮询
        ``GET /files/{file_id}/status`` 拿结果。完成后的 file_id 与同步上传等价，
        可传给 ``/chat`` 的 ``file_ids`` 使用。
        """
        from core.jobs import _current_task_id, get_queue

        session = _resolve_session_id(session_id)
        set_session_id(session)
        session_scope = tenant_scoped(session)
        get_session_registry().touch(session_scope)
        name = file.filename or "upload"
        max_bytes, allowed_exts = _upload_limits()
        _validate_upload_name(name, allowed_exts)
        data = await _read_upload_limited(file, max_bytes)

        def _parse_and_store() -> dict:
            """worker 内执行：解析 → 存 filestore（file_id = 当前任务 id）。

            解析放到**独立进程**（core.jobs.cpu_pool）：PDF 解析是 CPU/GIL 密集，
            跑在主进程会拖慢同期提问链路（实测 5 并发上传使提问 p95 3.41s→6.29s）。
            本回调仍在**主进程**（队列 worker 线程）里跑：解析产物要交给
            filestore，而子进程只做纯解析。多 worker 下 filestore 走 Redis，
            其他 worker 也能读到（见 core/filestore.py）。
            """
            fid = _current_task_id.get()
            res = _parse_upload(data, name)
            record = filestore.FileRecord(
                file_id=fid,
                source_name=res["source_name"],
                source_format=res["source_format"],
                markdown=res["markdown"],
                preview=res["preview"],
                chunk_count=res["chunk_count"],
                session_id=session_scope,
                created_at=time.time(),
            )
            filestore.put(record)
            # C1：索引先于任务完成态落库——轮询到 status=done 时索引必然已就绪
            # （否则上传后立刻提问会打空，退化成全文模式）
            doc_context.build_index_for(record)
            return {
                "file_id": fid,
                "source_name": res["source_name"],
                "source_format": res["source_format"],
                "preview": res["preview"],
                "chunk_count": res["chunk_count"],
                "session_id": session,
            }

        task_id = get_queue().submit(_parse_and_store)
        return {
            "file_id": task_id,
            "session_id": session,
            "status": "pending",
            "message": "解析中，轮询 GET /files/{file_id}/status 获取结果",
        }

    @app.get("/files/{file_id}/status")
    def file_status(
        file_id: str,
        session_id: Optional[str] = None,
    ) -> dict:
        """异步上传任务状态（W4-3）：pending | processing | done | error。

        done 时返回与同步上传一致的文件信息；error 时返回解析失败原因（可重传）。

        **契约：只需 file_id 即可查询**（无需 session_id）。file_id 是 128 位随机 id，
        本身即能力凭据；未带 session_id 时 `_resolve_session_id` 会**每次生成新会话**，
        若按会话过滤则"上传后轮询"必然失败（原先即如此 → 500）。
        真正需要归属校验的是 `/chat` 的 file_ids 注入路径（见 `_resolve_context_docs`）。
        """
        from core.jobs import get_queue

        session = _resolve_session_id(session_id)
        set_session_id(session)

        st = get_queue().status(file_id)
        if st is None:
            # 任务表没有 → 可能已完成并写入 filestore（回退查，不按会话过滤）
            rec = filestore.get(file_id)
            if rec is None:
                raise HTTPException(
                    status_code=404, detail="任务或文件不存在（或已过期）"
                )
            return {
                "file_id": file_id, "status": "done",
                "source_name": rec.source_name,
                "source_format": rec.source_format,
                "preview": rec.preview,
                "chunk_count": rec.chunk_count,
                "session_id": session,
            }
        if st["status"] in ("pending", "processing"):
            return {"file_id": file_id, "status": st["status"],
                    "session_id": session}
        if st["status"] == "error":
            return {"file_id": file_id, "status": "error",
                    "error": st["error"], "session_id": session}
        # done：任务已完成，filestore 应已有记录（不按会话过滤，见方法文档）
        rec = filestore.get(file_id)
        if rec is None:
            # filestore TTL 为 1 小时：过期后属"找不到了"，不是服务端错误
            raise HTTPException(status_code=404, detail="任务已完成，但文件记录已过期")
        return {
            "file_id": file_id, "status": "done",
            "source_name": rec.source_name,
            "source_format": rec.source_format,
            "preview": rec.preview,
            "chunk_count": rec.chunk_count,
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
        session_scope = tenant_scoped(session_id)  # W4-3：内部隔离（检查点/会话/文件）
        get_session_registry().touch(session_scope)  # W2-4：登记会话
        # 文件校验放在响应开始之前，失败返回正常 HTTP 404 而不是流中 error 事件。
        # C1 起这里还含文档内检索（阻塞式 embedding HTTP 调用）——放线程里执行，
        # 避免在 async 端点里卡住事件循环（100 并发下这是硬要求）
        context_docs = await anyio.to_thread.run_sync(
            _context_docs_for, req, session_scope
        )
        return StreamingResponse(
            _sse_stream(agent, req, session_id, context_docs),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()


def _worker_app() -> FastAPI:
    """多 worker 模式的应用工厂（uvicorn ``factory=True`` 调用）。

    为什么需要它：uvicorn 在 ``workers > 1`` 时**必须传 import string**（每个 worker
    进程会重新导入应用），此时 ``main()`` 不在 worker 里执行 → 日志与追踪的初始化
    不会生效。改用工厂后，**每个 worker 各自跑一次**初始化。
    """
    setup_logging()
    from core.observability.tracing import setup_tracing

    setup_tracing()
    return create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    # C7：多 worker / 多副本前的单机横向扩展。配合 Redis 限流/缓存 + Postgres 检查点
    # 才能保证多进程间「配额/缓存/会话」一致（否则每 worker 各算一套，会话还会串）。
    workers = max(1, int(os.getenv("API_WORKERS", "1")))

    if workers > 1:
        logger.info("启动 %d 个 uvicorn worker（每进程独立图实例）", workers)
        uvicorn.run(
            "api.server:_worker_app", factory=True,
            host=host, port=port, log_config=None, workers=workers,
        )
        return

    # W0-7：先配置根 logger，再让 uvicorn 不接管日志（log_config=None），
    # 保证应用侧 INFO 埋点（分支耗时 / SQL profile / 检索埋点）在服务态落盘。
    setup_logging()
    # E0-3：初始化 OTel 追踪（未配置 ENABLE_TRACING/密钥时 no-op）
    from core.observability.tracing import setup_tracing

    setup_tracing()
    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
