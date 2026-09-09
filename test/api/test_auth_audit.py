"""W3-0 鉴权与审计测试。"""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from api.middleware.auth import key_fingerprint
from core.observability.audit import record_audit, reset_audit
from core.observability.context import reset_context, set_api_key_id, set_request_id, set_session_id


class _AuditCapture(logging.Handler):
    """直接挂在 audit logger 上（该 logger propagate=False，caplog 收不到）。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_audit() -> _AuditCapture:
    handler = _AuditCapture()
    logging.getLogger("audit").addHandler(handler)
    return handler


class _FakeAgent:
    def invoke(self, question, thread_id="default", *, context_docs=None):
        return {
            "answer": "ok", "intent": "general_chat",
            "business_result": {"branch": "general_chat", "answer": "ok", "data": None},
        }

    def stream(self, question, thread_id="default", *, context_docs=None):
        yield {"type": "node", "node": "general_chat"}


def _client() -> TestClient:
    from api.server import create_app

    return TestClient(create_app(get_agent_fn=lambda: _FakeAgent()))


# ── 鉴权 ─────────────────────────────────────────────────────

def test_auth_disabled_when_no_keys(monkeypatch):
    monkeypatch.setenv("API_KEYS", "")
    client = _client()
    assert client.post("/chat", json={"question": "q"}).status_code == 200


def test_auth_rejects_missing_or_invalid_key(monkeypatch):
    monkeypatch.setenv("API_KEYS", "k-alpha,k-beta")
    client = _client()

    missing = client.post("/chat", json={"question": "q"})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "biz_1006"

    invalid = client.post(
        "/chat", json={"question": "q"}, headers={"X-API-Key": "nope"},
    )
    assert invalid.status_code == 401


def test_auth_accepts_api_key_and_bearer(monkeypatch):
    monkeypatch.setenv("API_KEYS", "k-alpha")
    client = _client()

    via_header = client.post(
        "/chat", json={"question": "q"}, headers={"X-API-Key": "k-alpha"},
    )
    assert via_header.status_code == 200

    via_bearer = client.post(
        "/chat", json={"question": "q"}, headers={"Authorization": "Bearer k-alpha"},
    )
    assert via_bearer.status_code == 200


def test_auth_exempts_probe_paths(monkeypatch):
    monkeypatch.setenv("API_KEYS", "k-alpha")
    client = _client()

    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_key_fingerprint_is_stable_and_not_the_key():
    fingerprint = key_fingerprint("k-alpha")
    assert fingerprint == key_fingerprint("k-alpha")
    assert len(fingerprint) == 8
    assert "k-alpha" not in fingerprint


# ── 审计 ─────────────────────────────────────────────────────

def test_record_audit_writes_json_line():
    reset_context()
    reset_audit()
    set_request_id("rid-1")
    set_session_id("sid-1")
    set_api_key_id("abcdef12")
    capture = _capture_audit()

    record_audit(action="chat", status="ok", question="招标方式有哪些？", latency_ms=123)

    payload = json.loads(capture.records[-1].getMessage())
    assert payload["action"] == "chat"
    assert payload["request_id"] == "rid-1"
    assert payload["session_id"] == "sid-1"
    assert payload["api_key_id"] == "abcdef12"
    assert payload["latency_ms"] == 123
    assert payload["question"] == "招标方式有哪些？"
    reset_context()


def test_record_audit_truncates_and_can_disable_question(monkeypatch):
    reset_audit()
    capture = _capture_audit()

    record_audit(action="chat", question="问" * 500)
    payload = json.loads(capture.records[-1].getMessage())
    assert len(payload["question"]) == 120

    monkeypatch.setenv("AUDIT_LOG_QUESTIONS", "false")
    record_audit(action="chat", question="敏感问题")
    payload = json.loads(capture.records[-1].getMessage())
    assert "question" not in payload


def test_record_audit_writes_file_when_configured(tmp_path, monkeypatch):
    reset_audit()
    log_file = tmp_path / "audit.log"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_file))

    record_audit(action="upload", status="ok", extra={"file": "a.txt"})

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["action"] == "upload"
    assert payload["file"] == "a.txt"
    reset_audit()


def test_chat_endpoint_records_audit(monkeypatch):
    monkeypatch.setenv("API_KEYS", "k-alpha")
    reset_audit()
    capture = _capture_audit()
    client = _client()

    response = client.post(
        "/chat", json={"question": "你好", "session_id": "s1"},
        headers={"X-API-Key": "k-alpha"},
    )

    assert response.status_code == 200
    payload = json.loads(capture.records[-1].getMessage())
    assert payload["action"] == "chat"
    assert payload["status"] == "ok"
    assert payload["session_id"] == "s1"
    assert payload["api_key_id"] == key_fingerprint("k-alpha")
    assert payload["question"] == "你好"
