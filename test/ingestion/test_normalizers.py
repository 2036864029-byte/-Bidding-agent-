"""Normalizer 层（E-4~E-6）离线测试：text 直通 + csv 双 IR。"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import pytest

from public_kb.ingestion.ir import NormalizedDocument
from public_kb.ingestion.normalizers import (
    CsvNormalizer,
    PdfNormalizer,
    TextNormalizer,
    build_normalizer,
)
from public_kb.ingestion.router import FormatRouter


# ── E-5 TextNormalizer ─────────────────────────────────────

def test_text_normalizer_passthrough(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 标题\n正文内容", encoding="utf-8")
    routed = FormatRouter().route(p)
    out = TextNormalizer().normalize(routed)
    assert len(out) == 1
    doc = out[0]
    assert doc.ir_format == "markdown"
    assert doc.content == "# 标题\n正文内容"
    assert doc.metadata["quality_score"] == 1.0
    assert doc.metadata["parser_version"] == "text/v1"
    assert doc.metadata["source_hash"] == routed.metadata["source_hash"]


# ── E-6 CsvNormalizer ──────────────────────────────────────

def _csv_doc(tmp_path, name: str, text: str) -> NormalizedDocument:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return FormatRouter().route(p)


def test_csv_table_ir_columns_and_row_count(tmp_path):
    routed = _csv_doc(
        tmp_path, "company.csv",
        "company_name,registered_capital\n甲公司,100万\n乙公司,200万\n",
    )
    docs = CsvNormalizer().normalize(routed)
    table = next(d for d in docs if d.ir_format == "table")
    assert table.metadata["columns"] == ["company_name", "registered_capital"]
    assert table.metadata["row_count"] == 2
    assert table.metadata["truncated"] is False
    assert table.content.startswith("| company_name | registered_capital |")
    assert "甲公司" in table.content


def test_csv_table_ir_sample_cap(tmp_path):
    header = "col1,col2\n"
    body = "".join(f"v{i},w{i}\n" for i in range(50))
    routed = _csv_doc(tmp_path, "big.csv", header + body)
    docs = CsvNormalizer(sample_rows=10).normalize(routed)
    table = next(d for d in docs if d.ir_format == "table")
    assert table.metadata["row_count"] == 50
    assert table.metadata["truncated"] is True
    # 内容只含 10 采样行
    assert table.content.count("| v") == 10


def test_csv_policy_rows_dual_ir(tmp_path):
    routed = _csv_doc(
        tmp_path, "policy.csv",
        "title,content\n法条,第一条 为了规范招标投标活动\n第二条 内容\n",
    )
    docs = CsvNormalizer().normalize(routed)
    irs = {d.ir_format for d in docs}
    assert irs == {"table", "markdown"}
    md = next(d for d in docs if d.ir_format == "markdown")
    assert "第一条 为了规范招标投标活动" in md.content
    assert md.metadata["parser_version"] == "csv/rows-v1"


def test_csv_structured_only_table_ir(tmp_path):
    """结构化 csv（无 content/QA 列）→ 仅 table IR，向量路为空。"""
    routed = _csv_doc(
        tmp_path, "structured.csv",
        "company_name,credit_code\n甲公司,ABC123\n",
    )
    docs = CsvNormalizer().normalize(routed)
    assert [d.ir_format for d in docs] == ["table"]


def test_csv_missing_file_returns_empty(tmp_path):
    routed = NormalizedDocument(
        doc_id=str(tmp_path / "missing.csv"), source_format="csv",
        ir_format="table", content="", source_path=str(tmp_path / "missing.csv"),
    )
    assert CsvNormalizer().normalize(routed) == []


# ── build_normalizer 工厂 ──────────────────────────────────

def test_build_normalizer_dispatch():
    assert isinstance(build_normalizer("text"), TextNormalizer)
    assert isinstance(build_normalizer("csv"), CsvNormalizer)
    assert isinstance(build_normalizer("pdf"), PdfNormalizer)
    with pytest.raises(ValueError):
        build_normalizer("unknown")


# ── E-7 PdfNormalizer（注入 fake parser，不触 MinerU） ─────

def test_pdf_normalizer_with_injected_parser(tmp_path):
    from public_kb.ingestion.normalizers.pdf.normalizer import PdfNormalizer

    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF fake")
    routed = FormatRouter().route(p)
    norm = PdfNormalizer(parser=_FakeParser("# 标题\n解析正文"))
    docs = norm.normalize(routed)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.ir_format == "markdown"
    assert doc.content == "# 标题\n解析正文"
    assert doc.metadata["quality_score"] == 1.0
    assert doc.metadata["parser_version"] == "pdf/v1"


class _FakeParser:
    def __init__(self, markdown: str) -> None:
        self._md = markdown

    def parse(self, path):  # noqa: ARG002
        return self._md
