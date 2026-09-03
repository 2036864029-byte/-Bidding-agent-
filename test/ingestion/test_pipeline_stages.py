"""三段式文件入库 pipeline（E-8）离线测试。全 fake sink/ledger，不触 Milvus。"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.ledger import IngestLedger
from public_kb.ingestion.pipeline import FileIngestionPipeline


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_max_chars=2000,
        chunk_overlap_chars=100,
        enable_pdf_structure=False,
        pdf_min_table_rows=2,
        enable_pdf_toc_filter=False,
        enable_pdf_reflow_flag=False,
    )


class FakeSink:
    def __init__(self) -> None:
        self.writes: list[list] = []

    def write(self, documents, *, records):  # noqa: ARG001
        self.writes.append(list(documents))
        return len(documents)


# ── 文本：markdown IR → 分块 → sink ────────────────────────

def test_text_file_three_stage(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 标题\n这是第一段足够长的正文内容说明。\n这是第二段同样足够长的正文内容说明。",
                 encoding="utf-8")
    sink = FakeSink()
    res = FileIngestionPipeline(_settings(), sink=sink).run([p])[0]
    assert res.status == "completed"
    assert res.source_format == "text"
    assert res.chunk_count >= 1
    assert res.inserted_count == res.chunk_count
    assert len(sink.writes) == 1
    docs = sink.writes[0]
    assert all("chunk_uid" in d.metadata for d in docs)
    assert all(d.metadata["source_format"] == "text" for d in docs)


# ── 结构化 csv：仅 table IR，不入向量路 ─────────────────────

def test_structured_csv_table_ir_no_sink(tmp_path):
    p = tmp_path / "company.csv"
    p.write_text("company_name,registered_capital\n甲公司,100万\n乙公司,200万\n",
                 encoding="utf-8")
    sink = FakeSink()
    res = FileIngestionPipeline(_settings(), sink=sink).run([p])[0]
    assert res.status == "completed"
    assert res.table_irs == 1
    assert res.chunk_count == 0
    assert res.inserted_count == 0
    assert sink.writes == []  # 无 markdown IR → 不调 sink
    assert res.detail["table_columns"] == [["company_name", "registered_capital"]]
    assert res.detail["table_row_count"] == 2


# ── 政策 csv：双 IR，markdown 走向量路 ──────────────────────

def test_policy_csv_dual_ir_chunks(tmp_path):
    p = tmp_path / "policy.csv"
    p.write_text("title,content\n法条,第一条 为了规范招标投标活动，保护国家利益和社会公共利益。\n",
                 encoding="utf-8")
    sink = FakeSink()
    res = FileIngestionPipeline(_settings(), sink=sink).run([p])[0]
    assert res.status == "completed"
    assert res.table_irs == 1
    assert res.chunk_count >= 1
    assert res.inserted_count == res.chunk_count
    assert len(sink.writes) == 1
    assert all(d.metadata["source_format"] == "csv" for d in sink.writes[0])


# ── ledger：文档级去重 → 跳过 ───────────────────────────────

def test_ledger_skips_second_identical(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 标题\n这是足够长的正文内容说明。", encoding="utf-8")
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    sink = FakeSink()
    pipe = FileIngestionPipeline(_settings(), sink=sink, ledger=ledger)
    first = pipe.run([p])[0]
    assert first.status == "completed"
    second = pipe.run([p])[0]
    assert second.status == "skipped"
    assert second.reason == "exact_dup"
    assert len(sink.writes) == 1  # 第二次未再写


# ── 预览模式（无 sink）─────────────────────────────────────

def test_preview_mode_no_sink(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 标题\n这是足够长的正文内容说明。", encoding="utf-8")
    res = FileIngestionPipeline(_settings(), sink=None).run([p])[0]
    assert res.status == "completed"
    assert res.chunk_count >= 1
    assert res.inserted_count == 0


# ── 失败文件 ───────────────────────────────────────────────

def test_missing_file_fails(tmp_path):
    res = FileIngestionPipeline(_settings(), sink=FakeSink()).run(
        [tmp_path / "nope.md"]
    )[0]
    assert res.status == "failed"
    assert res.reason
