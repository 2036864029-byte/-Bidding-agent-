"""channels.feishu.backend — /chat 客户端（MockTransport，不真连后端）。"""

import json

import httpx
import pytest

from channels.feishu.backend import BackendClient, BackendError


def _client(handler) -> BackendClient:
    transport = httpx.MockTransport(handler)
    return BackendClient(
        "http://127.0.0.1:8000", http_client=httpx.Client(transport=transport)
    )


def test_chat_posts_expected_shape_and_headers():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["json"] = json.loads(req.content)
        seen["tenant"] = req.headers.get("X-Tenant-ID")
        seen["request_id"] = req.headers.get("X-Request-ID")
        return httpx.Response(200, json={"answer": "ok", "reply_type": "chat"})

    c = _client(handler)
    out = c.chat(
        "招标方式有哪些",
        session_id="fs-p2p-ou_user_a",
        tenant_id="feishu:ou_user_a",
        request_id="ev_1",
    )
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/chat")
    assert seen["json"] == {
        "question": "招标方式有哪些",
        "session_id": "fs-p2p-ou_user_a",
        "stream": False,
    }
    assert seen["tenant"] == "feishu:ou_user_a"
    assert seen["request_id"] == "ev_1"
    assert out["answer"] == "ok"


def test_chat_sends_api_key_when_configured():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["api_key"] = req.headers.get("X-API-Key")
        return httpx.Response(200, json={"answer": "ok", "reply_type": "chat"})

    transport = httpx.MockTransport(handler)
    c = BackendClient(
        "http://127.0.0.1:8000",
        api_key="k-123",
        http_client=httpx.Client(transport=transport),
    )
    c.chat("q", session_id="s", tenant_id="feishu:ou_x")
    assert seen["api_key"] == "k-123"


def test_chat_non_2xx_raises_backend_error():
    c = _client(lambda req: httpx.Response(503, text="busy"))
    with pytest.raises(BackendError):
        c.chat("q", session_id="s", tenant_id="feishu:ou_x")


def test_chat_connection_error_wrapped():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    c = _client(handler)
    with pytest.raises(BackendError):
        c.chat("q", session_id="s", tenant_id="feishu:ou_x")


def test_chat_timeout_wrapped():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    c = _client(handler)
    with pytest.raises(BackendError):
        c.chat("q", session_id="s", tenant_id="feishu:ou_x")


def test_chat_non_json_response_wrapped():
    c = _client(lambda req: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(BackendError):
        c.chat("q", session_id="s", tenant_id="feishu:ou_x")
