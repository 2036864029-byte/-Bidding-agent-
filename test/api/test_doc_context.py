"""api.doc_context 测试（C1 胶水层）— 假 embedding，不触网络。

覆盖：长/小文档的路由（检索 vs 全文）、索引缺失/关闭/异常的回退、
以及**渲染 ↔ 节点解析的跨模块契约**（块标记往返不丢字段）。
"""

from __future__ import annotations

import api.doc_context as dc
from api.file_store import FileRecord
from core import doc_index
from core.config.settings import Settings


def _markdown(sections: int) -> str:
    parts = ["# 招标文件"]
    for i in range(sections):
        parts.append(
            f"## 第{i + 1}章 条款{i + 1}\n"
            f"本条规定了第{i + 1}项具体要求，投标保证金金额为{i + 1}万元，"
            f"工期要求 {i + 1} 日历天。"
        )
    return "\n".join(parts)


def _record(markdown: str, *, file_id="f1", session_id="s1", chunk_count=None) -> FileRecord:
    rec = FileRecord(
        file_id=file_id, source_name="招标文件.pdf", source_format="pdf",
        markdown=markdown, preview=markdown[:60], chunk_count=chunk_count or 0,
        session_id=session_id, created_at=0.0,
    )
    if chunk_count is None:
        rec.chunk_count = len(dc.chunk_record(rec, Settings()))
    return rec


def test_build_index_skipped_for_small_doc(fake_doc_embeddings):
    """小文档全文本来就装得下，检索反而可能漏 → 不建索引（省一次向量化）。"""
    rec = _record(_markdown(2))

    assert dc.build_index_for(rec) is False
    assert doc_index.load(rec.file_id, rec.session_id) is None


def test_build_index_skipped_when_disabled(fake_doc_embeddings, monkeypatch):
    monkeypatch.setenv("DOC_INDEX_ENABLED", "false")
    rec = _record(_markdown(15))

    assert dc.build_index_for(rec) is False


def test_build_index_for_long_doc(fake_doc_embeddings):
    rec = _record(_markdown(15))

    assert rec.chunk_count > 12
    assert dc.build_index_for(rec) is True

    index = doc_index.load(rec.file_id, rec.session_id)
    assert index is not None and len(index.chunks) == rec.chunk_count


def test_build_index_returns_false_on_chunking_error(monkeypatch):
    """分块异常 → 回退全文模式，不抛给上传链路。"""
    monkeypatch.setattr(
        dc, "chunk_record", lambda record, settings: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    rec = _record(_markdown(15), chunk_count=15)

    assert dc.build_index_for(rec) is False


# ── 渲染 ─────────────────────────────────────────────────────────────


def test_render_uses_retrieval_for_long_doc(fake_doc_embeddings):
    rec = _record(_markdown(15))
    dc.build_index_for(rec)

    mode, content = dc.render_for_record(rec, "第7章的投标保证金金额是多少？")

    assert mode == dc.MODE_RETRIEVAL
    assert content.startswith(dc.RETRIEVAL_HEADER.split("{")[0])
    assert "[块 " in content


def test_render_uses_fulltext_for_small_doc(fake_doc_embeddings):
    rec = _record(_markdown(2))

    mode, content = dc.render_for_record(rec, "问题")

    assert mode == dc.MODE_FULLTEXT
    assert content == rec.markdown


def test_render_falls_back_to_fulltext_when_index_missing(fake_doc_embeddings):
    """未建索引（如上传时向量化失败）→ 回退全文，功能不降级为"不可用"。"""
    rec = _record(_markdown(15))

    mode, content = dc.render_for_record(rec, "问题")

    assert mode == dc.MODE_FULLTEXT
    assert content == rec.markdown


def test_render_falls_back_to_fulltext_when_disabled(fake_doc_embeddings, monkeypatch):
    rec = _record(_markdown(15))
    dc.build_index_for(rec)
    monkeypatch.setenv("DOC_INDEX_ENABLED", "false")

    mode, content = dc.render_for_record(rec, "问题")

    assert mode == dc.MODE_FULLTEXT
    assert content == rec.markdown


def test_render_falls_back_to_fulltext_on_search_error(fake_doc_embeddings, monkeypatch):
    rec = _record(_markdown(15))
    dc.build_index_for(rec)

    def _boom(*args, **kwargs):
        raise RuntimeError("检索挂了")

    monkeypatch.setattr(doc_index, "search_file", _boom)

    mode, content = dc.render_for_record(rec, "问题")

    assert mode == dc.MODE_FULLTEXT
    assert content == rec.markdown


def test_render_retrieval_headers_carry_scores(fake_doc_embeddings):
    rec = _record(_markdown(15))
    dc.build_index_for(rec)

    _mode, content = dc.render_for_record(rec, "第3章的投标保证金金额是多少？")

    header = content.splitlines()[0]
    assert header.startswith("[检索模式｜命中 ")
    assert "最高相关度" in header


# ── context_docs（多文件 / kind 透传）────────────────────────────────


def test_context_docs_passes_kind_and_keeps_order(fake_doc_embeddings):
    recs = [_record(_markdown(2), file_id="f1"), _record(_markdown(2), file_id="f2")]

    out = dc.context_docs(recs, "问题", kind="session")

    assert [entry[2] for entry in out] == ["session", "session"]
    assert [entry[0] for entry in out] == ["招标文件.pdf", "招标文件.pdf"]


def test_context_docs_defaults_to_explicit(fake_doc_embeddings):
    out = dc.context_docs([_record(_markdown(2))], "问题")

    assert out[0][2] == "explicit"


def test_context_docs_survives_render_exception(fake_doc_embeddings, monkeypatch):
    """单个文件渲染异常 → 该文件按全文注入，不影响其余文件。"""
    rec = _record(_markdown(15))
    monkeypatch.setattr(
        dc, "render_for_record",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    out = dc.context_docs([rec], "问题")

    assert out == [("招标文件.pdf", rec.markdown, "explicit")]


# ── 跨模块契约：渲染 → 节点解析 ──────────────────────────────────────


def test_rendered_retrieval_content_round_trips_through_node_parser(fake_doc_embeddings):
    """BLOCK_LINE / RETRIEVAL_HEADER 的字段必须被 doc_qa 的解析器完整取回。

    这是两个模块之间唯一的口头契约——一处改了格式而另一处没改，就是静默丢引用。
    """
    from agent.nodes.doc_qa import MODE_RETRIEVAL, parse_injected_docs

    rec = _record(_markdown(15))
    dc.build_index_for(rec)
    _mode, content = dc.render_for_record(rec, "第7章的投标保证金金额是多少？")

    body = f"[文件：{rec.source_name}]\n{content}"
    docs = parse_injected_docs(body)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.name == rec.source_name
    assert doc.mode == MODE_RETRIEVAL
    assert doc.blocks, "块标记未被解析出来"
    assert doc.max_score > 0
    for block in doc.blocks:
        assert block.content.strip()          # 正文没被标记行吃掉
        assert block.chapter
        assert block.chunk_index >= 0
        assert 0.0 <= block.score <= 1.0
