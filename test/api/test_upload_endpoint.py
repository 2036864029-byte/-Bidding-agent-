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
    created_at: float | None = None,
) -> None:
    from core.observability.context import tenant_scoped

    file_store.put(
        file_store.FileRecord(
            file_id=file_id,
            source_name=name,
            source_format="text",
            markdown=markdown,
            preview=markdown,
            chunk_count=1,
            session_id=tenant_scoped(session),  # W4-3：镜像上传端点的租户作用域
            created_at=file_store.time.time() if created_at is None else created_at,
        )
    )


def test_upload_parses_and_returns_file_id(monkeypatch):
    calls: dict = {}

    def fake_parse(data, name):
        calls["name"] = name
        return {"source_name": name, "source_format": "text",
                "markdown": "第一章 内容", "preview": "第一章 内容", "chunk_count": 1}

    # 注入接缝：真实实现是独立进程（core.jobs.cpu_pool），测试里换成假解析器
    monkeypatch.setattr("api.server._parse_upload", fake_parse)
    file_store.reset_store()
    client, _ = _client()

    r = client.post("/files/upload", files={"file": ("a.txt", b"hello", "text/plain")})

    assert r.status_code == 200
    body = r.json()
    assert body["file_id"]
    assert body["source_name"] == "a.txt"
    assert body["preview"] == "第一章 内容"
    assert file_store.get(body["file_id"]) is not None
    assert calls["name"] == "a.txt"


def test_upload_parse_failure_400(monkeypatch):
    def bad_parse(data, name):
        raise ValueError("未解析出可读文本")

    monkeypatch.setattr("api.server._parse_upload", bad_parse)
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
    """W0-11：解析不在调用线程执行（先经工作线程，再下沉独立进程）。

    解析是 CPU/GIL 重活：若在事件循环所在线程直接跑会卡住整个服务。
    """
    import threading

    seen: dict = {}

    def fake_parse(data, name):
        seen["thread"] = threading.get_ident()
        return {"source_name": name, "source_format": "text",
                "markdown": "M", "preview": "M", "chunk_count": 1}

    monkeypatch.setattr("api.server._parse_upload", fake_parse)
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
    # C1：显式 file_ids 的上下文条目带 kind="explicit"（弱信号"session"由 A3 自动附着产生）
    # 小文档（chunk_count=1 ≤ 阈值）走全文模式 → 上下文即 markdown 原文
    assert fake.last["context_docs"] == [("a.txt", "第一章 内容", "explicit")]


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
    monkeypatch.setattr(
        "api.server._parse_upload",
        lambda data, name: {"source_name": name, "source_format": "text",
                            "markdown": "M", "preview": "M", "chunk_count": 1},
    )
    file_store.reset_store()
    client, _ = _client()

    body = client.post(
        "/files/upload", files={"file": ("a.txt", b"hi", "text/plain")}
    ).json()

    sid = body["session_id"]
    assert sid and sid != "default"
    from core.observability.context import tenant_scoped

    assert file_store.get(body["file_id"], session_id=tenant_scoped(sid)) is not None
    assert file_store.get(body["file_id"], session_id=tenant_scoped("someone-else")) is None


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
    assert fake.last["context_docs"] == [("a.txt", "M", "explicit")]


# ── A3：会话文件自动附着 ─────────────────────────────────────


def test_chat_auto_attaches_session_file():
    """不带 file_ids 也能用本会话文件；条目带 session 标记（路由层按弱信号处理）。"""
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="第一章 内容", session="s1")
    client, fake = _client()

    r = client.post("/chat", json={"question": "投标保证金是多少？", "session_id": "s1"})

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("a.txt", "第一章 内容", "session")]


def test_explicit_file_ids_win_over_auto_attach():
    """客户端显式传 id 时以它为准（且保持强信号：kind=explicit，非 session）。"""
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="A", session="s1")
    _put_file(file_id="f2", name="b.txt", markdown="B", session="s1")
    client, fake = _client()

    r = client.post(
        "/chat", json={"question": "q", "file_ids": ["f2"], "session_id": "s1"}
    )

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("b.txt", "B", "explicit")]


def test_auto_attach_disabled_by_config(monkeypatch):
    monkeypatch.setenv("SESSION_FILE_AUTOLOAD_N", "0")
    file_store.reset_store()
    _put_file(file_id="f1", session="s1")
    client, fake = _client()

    r = client.post("/chat", json={"question": "q", "session_id": "s1"})

    assert r.status_code == 200
    assert fake.last["context_docs"] == []


def test_auto_attach_respects_limit_and_recency(monkeypatch):
    """N=1：只附着最近上传的那个。"""
    monkeypatch.setenv("SESSION_FILE_AUTOLOAD_N", "1")
    file_store.reset_store()
    now = file_store.time.time()
    _put_file(file_id="old", name="old.txt", markdown="O", session="s1", created_at=now - 10)
    _put_file(file_id="new", name="new.txt", markdown="N", session="s1", created_at=now)
    client, fake = _client()

    r = client.post("/chat", json={"question": "q", "session_id": "s1"})

    assert r.status_code == 200
    assert fake.last["context_docs"] == [("new.txt", "N", "session")]


def test_auto_attach_is_session_scoped():
    """别的会话的文件不得被自动附着（W0-6 同源要求）。"""
    file_store.reset_store()
    _put_file(file_id="f1", name="a.txt", markdown="A", session="s1")
    client, fake = _client()

    r = client.post("/chat", json={"question": "q", "session_id": "s2"})

    assert r.status_code == 200
    assert fake.last["context_docs"] == []


# ── C1：上传建索引 → 提问走文档内检索 ───────────────────────────────


def _long_markdown(sections: int) -> str:
    """多章节长文档（真实分块口径下块数 = 章节数）。"""
    parts = ["# 招标文件"]
    for i in range(sections):
        parts.append(
            f"## 第{i + 1}章 条款{i + 1}\n"
            f"本条规定了第{i + 1}项具体要求，投标保证金金额为{i + 1}万元，"
            f"工期要求 {i + 1} 日历天。"
        )
    return "\n".join(parts)


def _upload(client: TestClient, monkeypatch, markdown: str, chunk_count: int, name="大标书.pdf"):
    monkeypatch.setattr(
        "api.server._parse_upload",
        lambda data, n: {"source_name": name, "source_format": "pdf",
                         "markdown": markdown, "preview": markdown[:40],
                         "chunk_count": chunk_count},
    )
    r = client.post("/files/upload", files={"file": (name, b"x", "application/pdf")})
    assert r.status_code == 200
    return r.json()


def test_upload_builds_index_for_long_doc(fake_doc_embeddings, monkeypatch):
    from core import doc_index
    from core.observability.context import tenant_scoped

    file_store.reset_store()
    client, _ = _client()

    body = _upload(client, monkeypatch, _long_markdown(15), chunk_count=15)

    # 索引按**内部租户作用域**落键（与 file_store 记录、检查点同一口径）
    index = doc_index.load(body["file_id"], tenant_scoped(body["session_id"]))
    assert index is not None
    assert index.source_name == "大标书.pdf"


def test_upload_skips_index_for_small_doc(fake_doc_embeddings, monkeypatch):
    from core import doc_index
    from core.observability.context import tenant_scoped

    file_store.reset_store()
    client, _ = _client()

    body = _upload(client, monkeypatch, _long_markdown(2), chunk_count=2)

    assert doc_index.load(body["file_id"], tenant_scoped(body["session_id"])) is None


def test_chat_with_long_doc_injects_retrieval_blocks(fake_doc_embeddings, monkeypatch):
    """C1 端到端：/chat 拿到的是检索块（带块标记），不再是全文。"""
    file_store.reset_store()
    client, fake = _client()
    body = _upload(client, monkeypatch, _long_markdown(15), chunk_count=15)

    r = client.post(
        "/chat",
        json={"question": "第7章的投标保证金金额是多少？",
              "file_ids": [body["file_id"]], "session_id": body["session_id"]},
    )

    assert r.status_code == 200
    (name, content, kind) = fake.last["context_docs"][0]
    assert name == "大标书.pdf" and kind == "explicit"
    assert content.startswith("[检索模式｜命中 ")
    assert "[块 " in content
    assert content != _long_markdown(15)          # 不再是全文注入


def test_chat_falls_back_to_fulltext_when_index_disabled(fake_doc_embeddings, monkeypatch):
    """索引开关关闭时回退全文模式（旧行为），功能不因此不可用。"""
    monkeypatch.setenv("DOC_INDEX_ENABLED", "false")
    file_store.reset_store()
    client, fake = _client()
    body = _upload(client, monkeypatch, _long_markdown(15), chunk_count=15)

    r = client.post(
        "/chat",
        json={"question": "第7章的投标保证金金额是多少？",
              "file_ids": [body["file_id"]], "session_id": body["session_id"]},
    )

    assert r.status_code == 200
    _name, content, _kind = fake.last["context_docs"][0]
    assert content == _long_markdown(15)
    assert not content.startswith("[检索模式")
