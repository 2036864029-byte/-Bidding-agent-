"""core.doc_index 测试（C1）— 假 embedding，不触网络。

覆盖：建索引 / 混合检索排序 / L2 归一 / RRF 与 BM25 机制 / 会话隔离 /
**fail-open 降级**（向量化失败→None、查询向量化失败→纯 BM25）。
"""

from __future__ import annotations

import numpy as np
import pytest

from core import doc_index


def _chunks(*pairs) -> list[doc_index.DocChunk]:
    return [
        doc_index.DocChunk(chunk_index=i, chapter=chapter, content=content)
        for i, (chapter, content) in enumerate(pairs)
    ]


DOC_CHUNKS = _chunks(
    ("第一章 采购需求", "本项目采购内容为办公设备一批，工期要求 30 日历天。"),
    ("第二章 投标人须知", "投标保证金金额为人民币伍万元整，须在投标截止时间前缴纳。"),
    ("第三章 评标办法", "评标委员会成员人数为五人，其中技术专家不少于三人。"),
    ("第四章 合同条款", "合同价款支付方式为分期支付，质量验收标准见附件。"),
)


def _build(chunks=DOC_CHUNKS, file_id="f1", session_id="s1"):
    return doc_index.build(
        file_id=file_id, session_id=session_id, source_name="招标文件.pdf",
        chunks=chunks,
    )


# ── 建索引 ───────────────────────────────────────────────────────────


def test_build_stores_index_and_normalizes_vectors(fake_doc_embeddings):
    index = _build()

    assert index is not None
    assert index.vectors.shape[0] == len(DOC_CHUNKS)
    assert index.vectors.shape[1] > 0
    norms = np.linalg.norm(index.vectors, axis=1)
    np.testing.assert_allclose(norms, np.ones(len(DOC_CHUNKS)), atol=1e-5)
    assert index.model_id  # 供后续判断向量空间版本
    assert index.source_name == "招标文件.pdf"
    assert [c.chunk_index for c in index.chunks] == [0, 1, 2, 3]


def test_build_returns_none_for_empty_chunks(fake_doc_embeddings):
    assert _build(chunks=[]) is None


def test_build_returns_none_when_disabled(fake_doc_embeddings, monkeypatch):
    monkeypatch.setenv("DOC_INDEX_ENABLED", "false")

    assert _build() is None
    assert doc_index.load("f1", "s1") is None


def test_build_returns_none_when_embedding_fails(monkeypatch):
    """fail-open：向量化挂了只是回退全文模式，绝不抛给上传链路。"""
    import core.embeddings as ce

    def _boom(settings=None):
        raise RuntimeError("embedding 服务不可达")

    monkeypatch.setattr(ce, "get_embeddings", _boom)

    assert _build() is None
    assert doc_index.load("f1", "s1") is None


def test_build_returns_none_when_vector_count_mismatches(monkeypatch):
    """半成品索引比没有索引更糟：条数对不上就整体放弃。"""
    import core.embeddings as ce

    class _Short:
        def embed_documents(self, texts):
            return [[1.0, 0.0]] * (len(texts) - 1)

    monkeypatch.setattr(ce, "get_embeddings", lambda settings=None: _Short())

    assert _build() is None


# ── 检索排序 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "question,expected_index",
    [
        ("投标保证金金额是多少？", 1),
        ("评标委员会成员人数为几人？", 2),
        ("工期要求多少天", 0),
        ("合同价款支付方式", 3),
    ],
)
def test_search_ranks_relevant_chunk_first(fake_doc_embeddings, question, expected_index):
    index = _build()

    result = doc_index.search(index, question, top_k=2)

    assert not result.empty
    assert result.hits[0].chunk_index == expected_index
    assert result.low_confidence is False
    assert result.degraded is False


def test_search_caps_hits_at_chunk_count(fake_doc_embeddings):
    index = _build(chunks=DOC_CHUNKS[:2])

    result = doc_index.search(index, "投标保证金", top_k=10)

    assert len(result.hits) == 2


def test_search_pads_to_top_k_with_zero_signal_chunks(fake_doc_embeddings):
    """零信号块补齐到 top_k：上下文规模恒定，不会因"只有一块匹配"而塌缩。"""
    index = _build()

    result = doc_index.search(index, "投标保证金金额是多少", top_k=4)

    assert len(result.hits) == 4
    assert result.hits[0].chunk_index == 1                  # 有信号的排最前
    assert result.hits[0].score > result.hits[1].score      # 补齐块融合分为 0
    assert {h.chunk_index for h in result.hits} == {0, 1, 2, 3}


def test_search_reports_low_confidence_without_dropping_hits(fake_doc_embeddings):
    """阈值只用于**告知**，不用于丢命中——避免阈值误设导致"答不出来"。"""
    index = _build()

    result = doc_index.search(index, "今天天气怎么样", top_k=2, min_score=0.35)

    assert len(result.hits) == 2                      # 仍给出上下文
    assert result.low_confidence is True
    assert result.max_dense == 0.0


def test_search_isolated_per_session(fake_doc_embeddings):
    """W0-6 同类保证：别的会话读不到本会话的索引。"""
    _build(session_id="s1")

    assert doc_index.load("f1", "s1") is not None
    assert doc_index.load("f1", "s2") is None
    assert doc_index.search_file("f1", "s2", "投标保证金") is None


def test_search_isolated_per_file(fake_doc_embeddings):
    _build(file_id="f1")

    assert doc_index.load("f2", "s1") is None


def test_search_degrades_to_bm25_when_query_embedding_fails(
    fake_doc_embeddings, monkeypatch
):
    """查询向量化失败 → 纯 BM25 仍可用（degraded=True，dense 全 0）。"""
    index = _build()
    import core.embeddings as ce

    def _boom(settings=None):
        raise RuntimeError("查询向量化失败")

    # 夹具与用例共享同一个 monkeypatch 实例 → 覆盖后建索引已完成的既有状态
    monkeypatch.setattr(ce, "get_embeddings", _boom)

    result = doc_index.search(index, "投标保证金金额", top_k=2)

    assert result.degraded is True
    assert not result.empty
    assert result.hits[0].chunk_index == 1          # BM25 仍把含关键词的块排在最前
    assert all(hit.dense == 0.0 for hit in result.hits)


def test_search_without_any_signal_falls_back_to_document_head(fake_doc_embeddings):
    """查询词与文档零重叠时退回文档首部若干块（给上下文总比不给强）。"""
    index = _build()

    result = doc_index.search(index, "!!! ???", top_k=2)

    assert [hit.chunk_index for hit in result.hits] == [0, 1]


# ── 机制单测（BM25 / RRF / 查询词）─────────────────────────────────


def test_query_terms_mixes_words_and_cjk_bigrams():
    terms = doc_index._query_terms("投标保证金 GB/T 50319")

    assert {"投标", "标保", "保证", "证金"} <= set(terms)
    assert {"gb", "t", "50319"} <= set(terms)


def test_query_terms_handles_single_cjk_char():
    assert doc_index._query_terms("钱") == ["钱"]


def test_bm25_idf_favors_rare_term():
    chunks = _chunks(
        ("c1", "保证金"),
        ("c2", "保证金"),
        ("c3", "保证金"),
        ("c4", "保证金 履约期限"),
    )

    scores = doc_index._bm25_scores(chunks, ["履约", "保证"])

    # 罕见词"履约"只出现在 c4 → 其增益应高于出现在全部块里的"保证"
    assert scores[3] > scores[0]
    assert scores[1] == scores[2] == scores[0]


def test_bm25_returns_zeros_without_terms():
    assert doc_index._bm25_scores(DOC_CHUNKS, []) == [0.0] * len(DOC_CHUNKS)


def test_ranks_only_includes_positive_scores():
    assert doc_index._ranks([0.5, 0.0, 0.9]) == {2: 1, 0: 2}


def test_rrf_fuses_two_rankings(fake_doc_embeddings):
    """融合分 = 两路 1/(k+rank) 之和（一路第 1、另一路第 3 时介于两者之间）。"""
    index = _build()
    result = doc_index.search(index, "投标保证金金额", top_k=4)

    top = result.hits[0]
    expected_max = 1.0 / (doc_index._RRF_K + 1) + 1.0 / (doc_index._RRF_K + 1)
    assert top.score <= expected_max + 1e-6
    assert top.score == max(hit.score for hit in result.hits)


def test_source_view_matches_knowledge_qa_contract(fake_doc_embeddings):
    index = _build()
    hit = doc_index.search(index, "评标委员会成员人数", top_k=1).hits[0]

    src = hit.to_source("招标文件.pdf")

    assert set(src) == {"doc", "chapter", "chunk_index", "content_snippet", "score"}
    assert src["doc"] == "招标文件.pdf"
    assert src["chapter"] == "第三章 评标办法"
    assert src["score"] == round(hit.dense, 4)


def test_source_snippet_truncates_long_content():
    hit = doc_index.SearchHit(
        chunk_index=0, chapter="c", content="字" * 300, score=0.5, dense=0.5
    )

    src = hit.to_source("d.pdf")

    assert src["content_snippet"].endswith("...")
    assert len(src["content_snippet"]) == 203


# ── 生命周期 ─────────────────────────────────────────────────────────


def test_drop_removes_index(fake_doc_embeddings):
    _build()

    doc_index.drop("f1", "s1")

    assert doc_index.load("f1", "s1") is None


def test_search_file_returns_none_without_index(fake_doc_embeddings):
    assert doc_index.search_file("nope", "s1", "投标保证金") is None


def test_search_returns_empty_result_for_chunkless_index():
    index = doc_index.DocIndex(
        file_id="f", session_id="s", source_name="n",
        chunks=[], vectors=np.zeros((0, 3), dtype=np.float32),
    )

    result = doc_index.search(index, "任何问题")

    assert result.empty and result.max_dense is None
