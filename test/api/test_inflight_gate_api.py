"""C1 全局并发闸门的 API 层测试 —— 打满时快速失败（503 / SSE error），不排队。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.server import create_app
from core.concurrency.limits import get_inflight_gate, reset_inflight_gate


class FakeAgent:
    def invoke(self, question: str, thread_id: str = "default", *, context_docs=None) -> dict:
        return {
            "answer": "ok",
            "intent": "knowledge_qa",
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "ok",
                "data": {"sources": []},
            },
        }

    def stream(self, question: str, thread_id: str = "default", *, context_docs=None):
        yield {"type": "node", "node": "knowledge_qa"}
        yield {"type": "final", "answer": "ok"}


def _saturate(monkeypatch, *, limit: str = "1") -> None:
    """把闸门设为已占满（等待超时 0 → 立即失败，测试不阻塞）。"""
    monkeypatch.setenv("MAX_INFLIGHT_REQUESTS", limit)
    monkeypatch.setenv("INFLIGHT_WAIT_TIMEOUT_SECONDS", "0")
    reset_inflight_gate()
    gate = get_inflight_gate()
    assert gate.acquire() is True


def test_chat_returns_503_and_retry_after_when_saturated(monkeypatch):
    """在飞打满 → POST /chat 返回 503 + Retry-After + sys_2004（快速失败）。"""
    _saturate(monkeypatch)
    app = create_app(get_agent_fn=lambda: FakeAgent())
    resp = TestClient(app).post("/chat", json={"question": "招标方式有哪些？"})

    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")
    body = resp.json()
    assert body["error"]["code"] == "sys_2004"
    assert body["error"]["category"] == "system"


def test_chat_stream_emits_error_event_when_saturated(monkeypatch):
    """在飞打满 → SSE 发 error 事件（sys_2004）+ done 收尾，而非开线程排队。"""
    _saturate(monkeypatch)
    app = create_app(get_agent_fn=lambda: FakeAgent())
    resp = TestClient(app).post(
        "/chat/stream", json={"question": "招标方式有哪些？", "stream": True}
    )

    assert resp.status_code == 200          # 流已开始，错误在流内
    text = resp.text
    assert "event: error" in text
    assert "sys_2004" in text
    assert "event: done" in text


def test_chat_passes_when_gate_has_capacity(monkeypatch):
    """未打满 → 正常返回（闸门放行且正确释放）。"""
    monkeypatch.setenv("MAX_INFLIGHT_REQUESTS", "2")
    monkeypatch.setenv("INFLIGHT_WAIT_TIMEOUT_SECONDS", "0")
    reset_inflight_gate()
    gate = get_inflight_gate()

    app = create_app(get_agent_fn=lambda: FakeAgent())
    resp = TestClient(app).post("/chat", json={"question": "招标方式有哪些？"})

    assert resp.status_code == 200
    assert gate.inflight == 0               # 用完即还
