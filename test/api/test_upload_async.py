"""异步上传端点测试（W4-3 队列解耦）：/files/upload/async + /files/{id}/status。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client():
    from api.server import create_app
    from api.middleware.auth import ApiKeyAuthMiddleware

    return TestClient(create_app(get_agent_fn=lambda: _FakeAgent()))


class _FakeAgent:
    def invoke(self, question, thread_id="default", *, context_docs=None):
        return {"answer": "ok", "intent": "general_chat",
                "business_result": {"branch": "general_chat", "answer": "ok", "data": None}}


def _wait_done(client, file_id: str, session_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/files/{file_id}/status", params={"session_id": session_id})
        if r.status_code != 200:
            return {"http": r.status_code, "body": r.text}
        body = r.json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.02)
    return {"timeout": True}


def test_async_upload_then_status_done():
    """上传 txt → pending → 轮询到 done，file_id 可用。"""
    from api import file_store

    file_store.reset_store()
    client = _client()

    r = client.post(
        "/files/upload/async",
        files={"file": ("a.txt", "招标方式有哪几种？".encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    fid, sid = body["file_id"], body["session_id"]

    done = _wait_done(client, fid, sid)
    assert done["status"] == "done"
    assert done["source_name"] == "a.txt"
    assert done["source_format"] in ("text",)
    assert done["chunk_count"] >= 1
    assert "招标方式" in done["preview"]

    # 完成后 file_store 里有记录，/chat 的 file_ids 可用
    from core.observability.context import tenant_scoped

    assert file_store.get(fid, session_id=tenant_scoped(sid)) is not None


def test_async_upload_status_unknown_404():
    client = _client()
    r = client.get("/files/unknown-id/status", params={"session_id": "s1"})
    assert r.status_code == 404


def test_async_upload_rejects_bad_extension():
    client = _client()
    r = client.post(
        "/files/upload/async",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400
