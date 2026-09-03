"""QualityGate 质量关卡离线测试（方案 Phase 5 / E-11）。"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.ir import NormalizedDocument
from public_kb.ingestion.pipeline import FileIngestionPipeline
from public_kb.ingestion.quality import QualityGate


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_max_chars=2000,
        chunk_overlap_chars=100,
        enable_pdf_structure=False,
        pdf_min_table_rows=2,
        enable_pdf_toc_filter=False,
        enable_pdf_reflow_flag=False,
    )


def _ir(score) -> NormalizedDocument:
    return NormalizedDocument(
        doc_id="doc/1", source_format="text", ir_format="markdown",
        content="足够长的内容", metadata={"quality_score": score},
    )


class _Sink:
    def __init__(self) -> None:
        self.writes: list[list] = []

    def write(self, documents, *, records):  # noqa: ARG001
        self.writes.append(list(documents))
        return len(documents)


# ── QualityGate 单元 ───────────────────────────────────────

def test_partition_by_threshold():
    gate = QualityGate(threshold=0.8)
    ingest, review = gate.partition([_ir(0.9), _ir(0.3), _ir(None)])
    assert [i.metadata["quality_score"] for i in ingest] == [0.9, None]
    assert [r.metadata["quality_score"] for r in review] == [0.3]
    assert gate.should_review(None) is False  # 缺失视为达标


def test_dump_review_writes_file(tmp_path):
    gate = QualityGate(threshold=0.8, review_dir=tmp_path / "review")
    out = gate.dump_review(_ir(0.2), "doc/1")
    assert out.exists()
    assert "quality=0.2" in out.read_text(encoding="utf-8")


# ── pipeline 集成：低质量 → review 旁路，不入库 ─────────────

class _LowQualityNormalizer:
    source_format = "text"

    def normalize(self, source):
        return [
            NormalizedDocument(
                doc_id=f"{source.doc_id}#md", source_format="text",
                ir_format="markdown",
                content="这段内容足够长以避免被清洗器误删，用于验证质量关卡旁路入库。",
                source_path=source.source_path,
                metadata={"quality_score": 0.3, "parser_version": "test/low"},
            )
        ]


def test_pipeline_routes_low_quality_to_review(tmp_path, monkeypatch):
    import public_kb.ingestion.pipeline as pipe_mod

    monkeypatch.setattr(
        pipe_mod, "build_normalizer",
        lambda fmt, settings=None: _LowQualityNormalizer(),
    )
    p = tmp_path / "note.md"
    p.write_text("whatever", encoding="utf-8")
    sink = _Sink()
    gate = QualityGate(threshold=0.8, review_dir=tmp_path / "review")
    res = FileIngestionPipeline(_settings(), sink=sink, quality_gate=gate).run([p])[0]
    assert res.status == "review"
    assert res.reason == "quality_below_threshold"
    assert res.detail["review_count"] == 1
    assert sink.writes == []  # 不入库
    assert (tmp_path / "review" / "note.review.md").exists()


def test_pipeline_gate_off_ingests_low_quality(tmp_path, monkeypatch):
    import public_kb.ingestion.pipeline as pipe_mod

    monkeypatch.setattr(
        pipe_mod, "build_normalizer",
        lambda fmt, settings=None: _LowQualityNormalizer(),
    )
    p = tmp_path / "note.md"
    p.write_text("whatever", encoding="utf-8")
    sink = _Sink()
    res = FileIngestionPipeline(_settings(), sink=sink, quality_gate=None).run([p])[0]
    assert res.status == "completed"
    assert len(sink.writes) == 1  # 关卡关闭 → 照常入库
