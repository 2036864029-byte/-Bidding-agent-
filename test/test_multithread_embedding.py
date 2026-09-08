"""MilvusStoreManager._batch_insert 多线程 embedding 保序/批大小测试（离线）。"""

from __future__ import annotations

from langchain_core.documents import Document

from core.config.settings import Settings
from core.vectorstore.milvus_client import MilvusStoreManager


class FakeClient:
    def __init__(self) -> None:
        self.inserts: list[list[dict]] = []

    def insert(self, name: str, data: list[dict]) -> dict:
        self.inserts.append(data)
        return {"insert_count": len(data)}


class LengthEmbeddings:
    """按文本长度生成向量，用于校验并发后向量与文本映射不变。"""

    def __init__(self, dim: int = 3) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] * self.dim for t in texts]


def _settings(**overrides: object) -> Settings:
    values = {
        "milvus_uri": "http://offline.invalid:19530",
        "collection_name": "public_kb_hybrid_poc_thread",
        "embedding_dim": 3,
        "enable_dedup": False,
        "embedding_batch_size": 2,
        "embedding_max_workers": 3,
    }
    values.update(overrides)
    return Settings(**values)


def _docs() -> list[Document]:
    return [
        Document(
            page_content=t,
            metadata={"doc_name": "demo", "chapter": f"c{i}", "chunk_index": i},
        )
        for i, t in enumerate(["a", "ab", "abc", "abcd", "abcde"])
    ]


def test_batch_insert_uses_threaded_embedding_and_preserves_mapping():
    client = FakeClient()
    manager = MilvusStoreManager(_settings(), LengthEmbeddings(), client=client)

    inserted = manager._batch_insert(_docs())

    assert inserted == 5
    assert len(client.inserts) == 3  # batch_size=2 → 5 条分 3 批
    # 拼接所有批次记录，校验「文本 ↔ 向量」映射在并发下仍一一对应
    flat = [row for batch in client.inserts for row in batch]
    assert [row["text"] for row in flat] == ["a", "ab", "abc", "abcd", "abcde"]
    for row in flat:
        length = len(row["text"])
        assert row["vector"] == [float(length)] * 3


def test_batch_size_read_from_settings():
    client = FakeClient()
    settings = _settings(embedding_batch_size=1)
    manager = MilvusStoreManager(settings, LengthEmbeddings(), client=client)

    manager._batch_insert(_docs())

    assert len(client.inserts) == 5  # 每批 1 条
