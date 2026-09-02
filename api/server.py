# 功能：FastAPI 应用工厂 + /chat(invoke|SSE) + /health。
"""server（方案 Phase 2 / C-5）。

- create_app(get_agent=...) 工厂：便于测试注入 mock agent。
- POST /chat：stream=false → invoke 返回 ChatResponse；stream=true → SSE 逐事件转发。
- GET /health：agent 就绪标志。
- CORS origin 走 env CORS_ORIGINS（逗号分隔，默认 *）。
- 入口：python -m api.server（uvicorn，host/port 走 env，默认 127.0.0.1:8000）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Generator, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent import AgentGraph
from core.protocol.reply import to_reply
from core.protocol.renderers.api import reply_to_response

from .deps import get_agent
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


def _default_thread_id(session_id: Optional[str]) -> str:
    return session_id or "default"


def _invoke_response(agent: AgentGraph, req: ChatRequest) -> ChatResponse:
    t0 = time.perf_counter()
    result = agent.invoke(
        req.question,
        thread_id=_default_thread_id(req.session_id),
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    business_result = result.get("business_result") or {"branch": "fallback", "answer": "", "data": None}
    reply = to_reply(
        business_result,
        intent=str(result.get("intent", "unknown")),
        latency_ms=latency_ms,
    )
    return ChatResponse(**reply_to_response(reply))


def _sse_events(agent: AgentGraph, req: ChatRequest) -> Generator[str, None, None]:
    """把 AgentGraph.stream() 的图事件逐块转发为 SSE data。"""
    thread_id = _default_thread_id(req.session_id)
    try:
        for event in agent.stream(req.question, thread_id=thread_id):
            yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
    except Exception as exc:  # 流中异常以 error 事件收尾，不中断连接
        logger.error("SSE 异常: %s", exc)
        yield "event: error\ndata: " + json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n\n"
    yield "event: done\ndata: {}\n\n"


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(
    get_agent_fn: Callable[[], AgentGraph] = get_agent,
) -> FastAPI:
    """应用工厂。

    Args:
        get_agent_fn: 返回 AgentGraph 的依赖函数；测试可注入 mock。
    """
    app = FastAPI(title="ztb_demo Agent API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health(agent: AgentGraph = Depends(get_agent_fn)) -> dict:
        return {"status": "ok", "agent_ready": agent is not None}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, agent: AgentGraph = Depends(get_agent_fn)) -> ChatResponse:
        if req.stream:
            raise HTTPException(status_code=400, detail="stream=true 请用 POST /chat/stream（SSE）")
        return _invoke_response(agent, req)

    @app.post("/chat/stream")
    def chat_stream(req: ChatRequest, agent: AgentGraph = Depends(get_agent_fn)):
        if not req.stream:
            # 兼容：stream=false 打到 /chat/stream 时退化为普通响应
            return _invoke_response(agent, req)
        return StreamingResponse(
            _sse_events(agent, req),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
