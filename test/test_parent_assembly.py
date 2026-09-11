"""父块拼接（P2）与检索端同父去重（P1）的离线测试，不连 Milvus。"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.documents import Document

from core.config.settings import Settings
from public_kb.retrieval.parent_assembly import (
    assemble_parent_contexts,
    dedupe_by_parent,
)
from public_kb.retrieval.retriever import HybridRetriever


class FakeCollection:
    """最小 query 实现：只支持 ``parent_chunk_id in [...]`` 过滤。"""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self.queries: List[str] = []

    def query(
        self,
        collection_name: str,
        filter: str,
        output_fields: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        self.queries.append(filter)
        wanted = {
            item.strip().strip("\"'")
            for item in filter.split("in ", 1)[1].strip("[]").split(",")
            if item.strip()
        }
        return [row for row in self._rows if row["parent_chunk_id"] in wanted]


def _child(parent_id: str, seq: int, text: str) -> Document:
    return Document(
        page_content=text,
        metadata={
            "chunk_type": "child",
            "parent_chunk_id": parent_id,
            "sequence_in_parent": seq,
            "chunk_uid": f"ck-{parent_id}-{seq}",
        },
    )


def _rows(parent_id: str, texts: List[str]) -> List[Dict[str, Any]]:
    return [
        {"parent_chunk_id": parent_id, "sequence_in_parent": i, "text": text}
        for i, text in enumerate(texts, start=1)
    ]


def test_children_assembled_in_sequence_with_qa_prefix_dedup():
    """按 sequence 升序拼接；重复的「问：」前缀只保留首个；page_content 不变。"""
    collection = FakeCollection(_rows("qa_1|sec_1", [
        "问：什么是政府采购？\n\n答：第一段。",
        "问：什么是政府采购？\n\n答：第二段。",
        "问：什么是政府采购？\n\n答：第三段。",
    ]))
    hit = _child("qa_1|sec_1", 2, "问：什么是政府采购？\n\n答：第二段。")

    assemble_parent_contexts([hit], collection=collection, collection_name="c", max_chars=6000)

    context = hit.metadata["context_text"]
    assert context.count("问：什么是政府采购？") == 1
    assert context.index("答：第一段。") < context.index("答：第二段。") < context.index("答：第三段。")
    assert hit.page_content == "问：什么是政府采购？\n\n答：第二段。"


def test_atom_passes_through_without_query():
    """独立块自身即完整语义单元：不查库、不写 context_text。"""
    atom = Document(
        page_content="第一条 正文",
        metadata={"chunk_type": "atom", "parent_chunk_id": "ck-abc", "chunk_uid": "ck-abc"},
    )
    collection = FakeCollection([])

    assemble_parent_contexts([atom], collection=collection, collection_name="c", max_chars=6000)

    assert "context_text" not in atom.metadata
    assert collection.queries == []


def test_overlong_parent_degrades_to_neighbor_window():
    """超长父块降级为「命中子块 ±1 邻居」，缺边不取。"""
    texts = [f"问：问题？\n\n答：第{i}段内容。" + "补" * 100 for i in range(1, 11)]
    collection = FakeCollection(_rows("qa_2|sec_1", texts))
    hit = _child("qa_2|sec_1", 5, texts[4])

    assemble_parent_contexts([hit], collection=collection, collection_name="c", max_chars=400)

    context = hit.metadata["context_text"]
    assert "第4段" in context and "第5段" in context and "第6段" in context
    assert "第3段" not in context and "第7段" not in context


def test_still_overlong_degrades_to_hit_child_only():
    """±1 邻居仍超阈值时，只保留命中子块本身。"""
    texts = [f"问：问题？\n\n答：第{i}段。" + "补" * 300 for i in range(1, 6)]
    collection = FakeCollection(_rows("qa_3|sec_1", texts))
    hit = _child("qa_3|sec_1", 3, texts[2])

    assemble_parent_contexts([hit], collection=collection, collection_name="c", max_chars=400)

    assert hit.metadata["context_text"] == texts[2].strip()


def test_missing_parent_keeps_child_text():
    """父块子块取不回来时保留子块原文（不写 context_text），不抛异常。"""
    hit = _child("qa_9|sec_1", 1, "问：X？\n\n答：Y。")

    assemble_parent_contexts(
        [hit], collection=FakeCollection([]), collection_name="c", max_chars=6000
    )

    assert "context_text" not in hit.metadata


def _retriever(**overrides: Any) -> HybridRetriever:
    return HybridRetriever(
        vector_store=None,
        collection=None,
        embeddings=None,
        settings=Settings(**overrides),
        reranker=None,
    )


def test_rerank_candidate_k_expands_when_grouping_on():
    """分组开启时 rerank 多取候选（去重会丢弃同父碎片），关闭时维持 top_k。"""
    assert _retriever(
        retrieval_group_by_parent=True, retrieval_group_size=2, retrieval_top_k=5
    )._rerank_candidate_k() == 10
    assert _retriever(retrieval_group_by_parent=False)._rerank_candidate_k() == 11


def test_duplicate_parent_dedup_keeps_first_child():
    """同父兄弟子块只保留得分最高者（首个），不同父各自保留。"""
    retriever = _retriever(retrieval_group_by_parent=True)
    seen: set = set()

    assert retriever._is_duplicate_parent(_child("p1", 1, "a"), seen) is False
    assert retriever._is_duplicate_parent(_child("p1", 2, "b"), seen) is True
    assert retriever._is_duplicate_parent(_child("p2", 1, "c"), seen) is False


def test_dedup_disabled_keeps_legacy_behavior():
    """分组开关关闭时不去重（回到改造前行为，便于一键回退）。"""
    retriever = _retriever(retrieval_group_by_parent=False)
    seen: set = set()

    assert retriever._is_duplicate_parent(_child("p1", 1, "a"), seen) is False
    assert retriever._is_duplicate_parent(_child("p1", 2, "b"), seen) is False


def test_citation_metadata_carries_parent_chunk_id():
    """P3：引用元数据透传 parent_chunk_id，便于溯源引用所属父块（原文仍取子块）。"""
    from public_kb.guardrails.citations_build import build_citations

    doc = _child("qa_1|sec_1", 2, "问：X？\n\n答：Y。")
    doc.metadata.update({"doc_name": "d", "chapter": "c", "chunk_index": 0})

    citations = build_citations([(doc, 0.9)])

    assert citations[0].metadata.get("parent_chunk_id") == "qa_1|sec_1"
    assert citations[0].text == "问：X？\n\n答：Y。"


def test_dedupe_by_parent_keeps_first_of_each_parent():
    """降级路径共用：按 parent 去重，保留首次出现（=得分最高）的子块。"""
    docs = [
        (_child("p1", 1, "a"), 0.9),
        (_child("p1", 2, "b"), 0.8),
        (_child("p2", 1, "c"), 0.7),
    ]

    kept = dedupe_by_parent(docs)

    assert [doc.metadata["chunk_uid"] for doc, _ in kept] == ["ck-p1-1", "ck-p2-1"]
