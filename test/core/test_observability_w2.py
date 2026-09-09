"""W2-2 可观测底座测试：上下文 / 结构化日志 / 指标 / LLM 回调 / API 接线。"""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from core.observability.callbacks import LLMMetricsCallback, usage_from_response
from core.observability.context import (
    get_request_id,
    get_session_id,
    new_request_id,
    reset_context,
    set_request_id,
    set_session_id,
)
from core.observability.logging import (
    JsonFormatter,
    RequestContextFilter,
    resolve_json,
)
from core.observability.metrics import (
    record_branch_latency,
    record_llm_error,
    record_llm_latency,
    record_llm_tokens,
    render_metrics,
)

# ── 上下文 ───────────────────────────────────────────────────

def test_context_roundtrip():
    reset_context()
    try:
        rid = new_request_id()
        set_request_id(rid)
        set_session_id("s1")
        assert get_request_id() == rid
        assert get_session_id() == "s1"
    finally:
        reset_context()
    assert get_request_id() is None and get_session_id() is None


# ── 结构化日志 ───────────────────────────────────────────────

def test_json_formatter_injects_request_id():
    reset_context()
    set_request_id("rid-1")
    set_session_id("sid-1")
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    RequestContextFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "rid-1"
    assert payload["session_id"] == "sid-1"
    reset_context()


def test_resolve_json_env(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    assert resolve_json() is True
    assert resolve_json(json_output=False) is False
    monkeypatch.setenv("LOG_FORMAT", "text")
    assert resolve_json() is False


# ── 指标 ─────────────────────────────────────────────────────

def test_render_metrics_exposes_business_and_llm_metrics():
    record_branch_latency("knowledge_qa", 12.5)
    record_llm_latency("kb_generation", "deepseek-chat", 0.42)
    record_llm_tokens("kb_generation", "deepseek-chat", prompt=100, completion=20)
    record_llm_error("router", "deepseek-chat")

    text = render_metrics().decode("utf-8")

    assert "agent_branch_latency_seconds" in text
    assert "llm_latency_seconds" in text
    assert 'llm_tokens_total{kind="prompt",model="deepseek-chat",slot="kb_generation"}' in text
    assert 'llm_errors_total{model="deepseek-chat",slot="router"}' in text


# ── LLM 回调 ─────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, usage: dict):
        self.llm_output = {"token_usage": usage}
        self.generations = []


def test_usage_from_response_prefers_llm_output():
    assert usage_from_response(_FakeResponse(
        {"prompt_tokens": 7, "completion_tokens": 3},
    )) == (7, 3)


def test_llm_callback_records_tokens_and_latency():
    callback = LLMMetricsCallback("router", "deepseek-chat")
    callback.on_llm_start({}, ["hi"], run_id="run-1")
    callback.on_llm_end(_FakeResponse({"prompt_tokens": 5, "completion_tokens": 2}), run_id="run-1")

    text = render_metrics().decode("utf-8")
    assert 'llm_tokens_total{kind="completion",model="deepseek-chat",slot="router"}' in text


def test_llm_callback_error_is_counted():
    callback = LLMMetricsCallback("price_intent", "deepseek-chat")
    callback.on_llm_start({}, ["hi"], run_id="run-2")
    callback.on_llm_error(RuntimeError("boom"), run_id="run-2")

    assert 'llm_errors_total{model="deepseek-chat",slot="price_intent"}' in render_metrics().decode()


# ── API 接线 ─────────────────────────────────────────────────

class _FakeAgent:
    def invoke(self, question, thread_id="default", *, context_docs=None):
        return {
            "answer": "ok", "intent": "general_chat",
            "business_result": {"branch": "general_chat", "answer": "ok", "data": None},
        }


def _client() -> TestClient:
    from api.server import create_app

    return TestClient(create_app(get_agent_fn=lambda: _FakeAgent()))


def test_metrics_endpoint_ok():
    response = _client().get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_request_id_generated_and_echoed():
    client = _client()
    first = client.post("/chat", json={"question": "q"})
    assert first.headers["X-Request-ID"]

    second = client.post(
        "/chat", json={"question": "q"},
        headers={"X-Request-ID": "trace-abc"},
    )
    assert second.headers["X-Request-ID"] == "trace-abc"
