"""上传端点 + /chat file_ids 测试（9.3 Track D / D-3）— mock file_store/parse_file/agent。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api import file_store
from api.server import create_app


class FakeAgent:
    """mock AgentGraph：记录 thread_id 与 context_docs 透传。"""

    def __init__(self) -> None:
        self.last: dict | None = None

    def invoke(self, question: str, thread_id: str = "default", *, context_docs=None) -> dict:
        self.last = {"thread": thread_id, "context_docs": context_docs}
        return {
            "answer": "mock doc 答案",
            "intent": "doc_qa",
            "business_result": {
                "branch": "doc_qa",
                "answer": "mock doc 答案",
                "data": {"status": "success", "sources": [], "attached_docs": []},
            },
        }

    def stream(self, question: str, thread_id: str = "default", *, context_docs=None):
        self.last = {"thread": thread_id, "context_docs": context_docs}
        yield {"type": "node", "node": "doc_qa"}
        yield {"type": "final", "answer": "ok"}


def _client() -> tuple[TestClient, FakeAgent]:
    fake = FakeAgent()
    return TestClient(create_app(get_agent_fn=lambda: fake)), fake


def _put_file(
    file_id: str = "f1",
    name: str = "a.txt",
    markdown: str = "第一章 内容",
    session: str = "s1",
) -> None:
    file_store.put(
        file_store.FileRecord(
            file_id=file_id,
            source_name=name,
            source_format="text",
            markdown=markdown,
            preview=markdown,
            chunk_count=1,
            session_id=session,
            created_at=file_store.time.time(),
        )
    )


def test_upload_parses_and_returns_file_id(monkeypatch):
    from public_kb.ingestion.parse_tool import FileParseResult

    calls: dict = {}

    def fake_parse(data, *, source_name):
        calls["source_name"] = source_name
        return FileParseResult(
            source_name=source_name, source_format="text",
            markdown="第一章 内容", preview="第一章 内容", chunk_count=1,
        )

    monkeypatch.setattr("api.server.parse_file", fake_parse)
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/files/upload", files={"file": ("a.txt", b"hello", "text/plain")})

    assert r.status_code == 200
    body = r.json()
    assert body["file_id"]
    assert body["source_name"] == "a.txt"
    assert body["preview"] == "第一章 内容"
    assert file_store.get(body["file_id"]) is not None
    assert calls["source_name"] == "a.txt"


def test_upload_parse_failure_400(monkeypatch):
    def bad_parse(data, *, source_name):
        raise ValueError("未解析出可读文本")

    monkeypatch.setattr("api.server.parse_file", bad_parse)
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/files/upload", files={"file": ("x.txt", b"", "text/plain")})

    assert r.status_code == 400


def test_upload_rejects_unsupported_extension():
    """W0-8：扩展名白名单外的文件直接拒绝，不进解析链路。"""
    file_store.reset_store()
    client, _ = _client()

    r = client.post(
        "/files/upload",
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )

    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]


def test_upload_rejects_oversize(monkeypatch):
    """W0-8：超过大小上限返回 413，且不把整个文件读进内存。"""
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    file_store.reset_store()
    client, _ = _client()

    payload = b"x" * (1024 * 1024 + 1)
    r = client.post("/files/upload", files={"file": ("big.txt", payload, "text/plain")})

    assert r.status_code == 413


def test_upload_parses_in_worker_thread(monkeypatch):
    """W0-11：解析在工作线程执行，不阻塞事件循环。"""
    import threading

    from public_kb.ingestion.parse_tool import FileParseResult

    seen: dict = {}

    def fake_parse(data, *, source_name):
        seen["thread"] = threading.get_ident()
        return FileParseResult(
            source_name=source_name, source_format="text",
            markdown="M", preview="M", chunk_count=1,
        )

    monkeypatch.setattr("api.server.parse_file", fake_parse)
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/files/upload", files={"file": ("a.txt", b"hi", "text/plain")})

    assert r.status_code == 200
    assert seen["thread"] != threading.get_ident(), "解析必须在工作线程而非调用线程执行"


def test_chat_with_file_ids_passes_context(monkeypatch):
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="第一章 内容", session="s1")
    client, fake = _client()

    r = client.post(
        "/chat",
        json={"question": "帮我分析这个文档", "file_ids": ["f1"], "session_id": "s1"},
    )

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("a.txt", "第一章 内容")]


def test_file_of_other_session_is_rejected():
    """W0-6：不能把别的会话上传的文件注入自己的上下文。"""
    file_store.reset_store()
    _put_file(file_id="f1", session="s1")
    client, _ = _client()

    r = client.post(
        "/chat",
        json={"question": "q", "file_ids": ["f1"], "session_id": "s2"},
    )

    assert r.status_code == 404


def test_upload_returns_session_id_and_scopes_file(monkeypatch):
    """W0-6：上传未带 session_id 时生成并回传，文件归属该会话。"""
    from public_kb.ingestion.parse_tool import FileParseResult

    monkeypatch.setattr(
        "api.server.parse_file",
        lambda data, *, source_name: FileParseResult(
            source_name=source_name, source_format="text",
            markdown="M", preview="M", chunk_count=1,
        ),
    )
    file_store.reset_store()
    client, _ = _client()

    body = client.post(
        "/files/upload", files={"file": ("a.txt", b"hi", "text/plain")}
    ).json()

    sid = body["session_id"]
    assert sid and sid != "default"
    assert file_store.get(body["file_id"], session_id=sid) is not None
    assert file_store.get(body["file_id"], session_id="someone-else") is None


def test_chat_unknown_file_id_404():
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/chat", json={"question": "q", "file_ids": ["nope"]})

    assert r.status_code == 404


def test_chat_stream_with_file_ids_passes_context():
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="M", session="s1")
    client, fake = _client()

    r = client.post(
        "/chat/stream",
        json={"question": "q", "file_ids": ["f1"], "session_id": "s1", "stream": True},
    )

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("a.txt", "M")]
