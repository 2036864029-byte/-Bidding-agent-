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
    from core import filestore

    filestore.reset_store()
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

    assert filestore.get(fid, session_id=tenant_scoped(sid)) is not None


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


# ── 回归：状态查询不应依赖 session_id ─────────────────────────

def test_status_queryable_without_session_id():
    """回归：不带 session_id 上传 + 不带 session_id 轮询，必须可用。

    原 bug：`_resolve_session_id(None)` **每次调用生成新随机会话** → 上传与轮询落到
    不同会话 → 按会话过滤时"上传后轮询"**必然 500**（"任务完成但文件记录缺失"），
    文件虽解析成功却永远取不回。file_id 是 128 位随机 id，本身即能力凭据。
    """
    from core import filestore

    filestore.reset_store()
    client = _client()

    r = client.post(
        "/files/upload/async",
        files={"file": ("a.txt", "招标方式有哪几种？".encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    fid = r.json()["file_id"]

    body = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        resp = client.get(f"/files/{fid}/status")          # 刻意不带 session_id
        assert resp.status_code == 200, f"无 session 查询应 200，实得 {resp.status_code}: {resp.text}"
        body = resp.json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.02)

    assert body is not None and body["status"] == "done"
    assert body["source_name"] == "a.txt"
    assert "招标方式" in body["preview"]


def test_status_done_but_record_gone_returns_404_not_500():
    """任务已完成但 file_store 记录没了（TTL 过期）→ **404**，不是 500。

    500 意味着"服务端出错"，而"文件已过期找不到"是客户端可理解的正常结果。
    """
    from core import filestore

    filestore.reset_store()
    client = _client()

    r = client.post(
        "/files/upload/async",
        files={"file": ("a.txt", "内容".encode("utf-8"), "text/plain")},
    )
    fid = r.json()["file_id"]
    _wait_done(client, fid, r.json()["session_id"])

    filestore.reset_store()          # 模拟 TTL 过期把记录清掉（任务表仍记 done）
    resp = client.get(f"/files/{fid}/status")
    assert resp.status_code == 404
    assert "过期" in resp.json()["detail"]



# ── C1：异步链路同样要建文档索引（索引先于 done 落库）──────────────


def _long_markdown(sections: int) -> str:
    parts = ["# 招标文件"]
    for i in range(sections):
        parts.append(
            f"## 第{i + 1}章 条款{i + 1}\n"
            f"本条规定了第{i + 1}项具体要求，投标保证金金额为{i + 1}万元。"
        )
    return "\n".join(parts)


def test_async_upload_builds_doc_index(fake_doc_embeddings, monkeypatch):
    """轮询到 done 时索引必然已就绪——否则上传后立刻提问会打空、退化成全文。"""
    from core import filestore
    from core import doc_index
    from core.observability.context import tenant_scoped

    md = _long_markdown(15)
    monkeypatch.setattr(
        "api.server._parse_upload",
        lambda data, name: {"source_name": name, "source_format": "pdf",
                            "markdown": md, "preview": md[:40], "chunk_count": 15},
    )
    filestore.reset_store()
    client = _client()

    r = client.post("/files/upload/async", files={"file": ("大.pdf", b"x", "application/pdf")})
    fid, sid = r.json()["file_id"], r.json()["session_id"]

    done = _wait_done(client, fid, sid)
    assert done["status"] == "done"
    assert doc_index.load(fid, tenant_scoped(sid)) is not None


def test_async_upload_skips_index_for_small_doc(fake_doc_embeddings):
    from core import filestore
    from core import doc_index
    from core.observability.context import tenant_scoped

    filestore.reset_store()
    client = _client()

    r = client.post(
        "/files/upload/async",
        files={"file": ("a.txt", "招标方式有哪几种？".encode("utf-8"), "text/plain")},
    )
    fid, sid = r.json()["file_id"], r.json()["session_id"]
    _wait_done(client, fid, sid)

    assert doc_index.load(fid, tenant_scoped(sid)) is None
