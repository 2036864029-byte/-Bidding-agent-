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


def _put_file(file_id: str = "f1", name: str = "a.txt", markdown: str = "第一章 内容") -> None:
    file_store.put(
        file_store.FileRecord(
            file_id=file_id,
            source_name=name,
            source_format="text",
            markdown=markdown,
            preview=markdown,
            chunk_count=1,
            session_id="default",
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


def test_chat_with_file_ids_passes_context(monkeypatch):
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="第一章 内容")
    client, fake = _client()

    r = client.post("/chat", json={"question": "帮我分析这个文档", "file_ids": ["f1"]})

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("a.txt", "第一章 内容")]


def test_chat_unknown_file_id_404():
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/chat", json={"question": "q", "file_ids": ["nope"]})

    assert r.status_code == 404


def test_chat_stream_with_file_ids_passes_context():
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="M")
    client, fake = _client()

    r = client.post(
        "/chat/stream",
        json={"question": "q", "file_ids": ["f1"], "stream": True},
    )

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("a.txt", "M")]
