"""FormatRouter + IngestLedger 离线测试（方案 Phase 5 / E-2、E-3）。"""

from __future__ import annotations

import hashlib
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.ir import compute_source_hash, simhash
from public_kb.ingestion.ledger import IngestLedger
from public_kb.ingestion.router import FormatRouter

# ── E-2 FormatRouter ───────────────────────────────────────

def test_router_classifies_by_extension(tmp_path):
    router = FormatRouter()
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "b.csv").write_text("title,content\nx,y", encoding="utf-8")
    (tmp_path / "c.txt").write_text("hello", encoding="utf-8")
    assert router.classify(tmp_path / "a.pdf") == "pdf"
    assert router.classify(tmp_path / "b.csv") == "csv"
    assert router.classify(tmp_path / "c.txt") == "text"


def test_router_sniffs_pdf_without_extension(tmp_path):
    p = tmp_path / "noext"
    p.write_bytes(b"%PDF-1.4 fake content")
    assert FormatRouter().classify(p) == "pdf"


def test_router_sniffs_csv_without_extension(tmp_path):
    p = tmp_path / "noext"
    p.write_text("公司名称,注册资本\n甲公司,100万", encoding="utf-8")
    assert FormatRouter().classify(p) == "csv"


def test_router_text_route_produces_markdown_ir(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 标题\n正文", encoding="utf-8")
    doc = FormatRouter().route(p)
    assert doc.source_format == "text"
    assert doc.ir_format == "markdown"
    assert doc.content == "# 标题\n正文"
    assert doc.metadata["quality_score"] == 1.0
    assert doc.metadata["source_hash"] == compute_source_hash(p)


def test_router_pdf_route_is_placeholder(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    doc = FormatRouter().route(p)
    assert doc.source_format == "pdf"
    assert doc.content == ""  # 完整规范化由 normalizer(E-7) 完成


# ── E-3 IngestLedger ───────────────────────────────────────

def _hash(text: str) -> str:
    """文本内容哈希（等价源文件字节 MD5 的测试替身）。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def test_ledger_new_then_exact_dup(tmp_path):
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    h = _hash("第一条 内容")
    assert ledger.should_process("doc/1", h).process is True
    ledger.mark_done("doc/1", h, source_format="text")
    dec = ledger.should_process("doc/1", h)
    assert dec.process is False and dec.reason == "exact_dup"


def test_ledger_content_dup_cross_filename(tmp_path):
    """同一字节内容换文件名 → content_dup 跳过（真实场景：同书重扫）。"""
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    h = _hash("招标公告 原文内容")
    ledger.mark_done("raw_pdfs/a.pdf", h, source_format="pdf")
    dec = ledger.should_process("raw_pdfs/b.pdf", h)
    assert dec.process is False and dec.reason == "content_dup"
    assert dec.matched_doc_id == "raw_pdfs/a.pdf"


def test_ledger_content_changed_reprocesses(tmp_path):
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    h1 = _hash("v1 内容")
    ledger.mark_done("doc/1", h1)
    dec = ledger.should_process("doc/1", _hash("v2 内容"))
    assert dec.process is True and dec.reason == "changed"


def test_ledger_failed_entry_retries(tmp_path):
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    h = _hash("内容")
    ledger.mark_done("doc/1", h, status="failed")
    assert ledger.should_process("doc/1", h).reason == "retry_failed"


def test_ledger_persists_roundtrip(tmp_path):
    p = tmp_path / "ledger.jsonl"
    ledger = IngestLedger(p)
    h = _hash("内容")
    ledger.mark_done("doc/1", h, source_format="csv")
    reloaded = IngestLedger(p)
    assert len(reloaded) == 1
    assert reloaded.should_process("doc/1", h).reason == "exact_dup"
    # 跨文件名内容去重在重载后仍生效
    assert reloaded.should_process("doc/2", h).reason == "content_dup"


def test_ledger_skips_corrupt_lines(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        '{"doc_id": "ok", "source_hash": "x", "status": "processed"}\n'
        "not-json-line\n"
        '{"doc_id": "ok2", "source_hash": "y", "status": "processed"}\n',
        encoding="utf-8",
    )
    ledger = IngestLedger(p)
    assert len(ledger) == 2


def test_simhash_recorded_for_diagnostics(tmp_path):
    """simhash 作为登记字段保留（诊断用），不参与自动判定。"""
    ledger = IngestLedger(tmp_path / "ledger.jsonl")
    h = _hash("内容")
    ledger.mark_done("doc/1", h, simhash=simhash("内容"))
    assert ledger._by_id["doc/1"]["simhash"] == simhash("内容")
