"""CLI 三段式文件入库（E-12）离线测试：参数 + run_file_ingestion（注入 fake manager）。"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.cli import build_parser, run_file_ingestion


class _FakeManager:
    """按 chunk_uid 判重的 MilvusStoreManager 替身。"""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.add_calls = 0

    def add_documents(self, documents):
        from public_kb.chunk_ids import compute_chunk_uid

        self.add_calls += 1
        inserted = 0
        for doc in documents:
            uid = doc.metadata.get("chunk_uid") or compute_chunk_uid(
                doc.page_content, doc.metadata
            )
            if uid not in self.seen:
                self.seen.add(uid)
                inserted += 1
        return inserted


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_max_chars=2000,
        chunk_overlap_chars=100,
        enable_pdf_structure=False,
        pdf_min_table_rows=2,
        enable_pdf_toc_filter=False,
        enable_pdf_reflow_flag=False,
    )


def test_parser_accepts_three_stage_flags():
    args = build_parser().parse_args([
        "--files", "data/",
        "--ledger", "led.jsonl",
        "--work-dir", "work/",
        "--resume",
        "--rerun-from", "B",
        "--quality-threshold", "0.8",
        "--legacy",
    ])
    assert args.files == "data/"
    assert args.ledger == "led.jsonl"
    assert args.work_dir == "work/"
    assert args.resume is True
    assert args.rerun_from == "B"
    assert args.quality_threshold == 0.8
    assert args.legacy is True


def test_run_file_ingestion_text_and_csv(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.md").write_text(
        "# 标题\n这是足够长的正文内容说明，用于 CLI 三段式入库。", encoding="utf-8"
    )
    (tmp_path / "src" / "company.csv").write_text(
        "company_name,credit_code\n甲公司,ABC123\n", encoding="utf-8"
    )
    mgr = _FakeManager()
    results = run_file_ingestion(
        tmp_path / "src", _settings(), manager=mgr,
    )
    by_path = {r.path: r for r in results}
    note = by_path[str(tmp_path / "src" / "note.md")]
    assert note.status == "completed"
    assert note.chunk_count >= 1 and note.inserted_count >= 1
    company = by_path[str(tmp_path / "src" / "company.csv")]
    assert company.status == "completed"
    assert company.table_irs == 1  # 结构化 csv → table IR，不入向量
    assert company.inserted_count == 0
    assert mgr.add_calls == 1  # 仅 markdown 文件触发写入


def test_run_file_ingestion_resume_skips(tmp_path):
    (tmp_path / "src").mkdir()
    p = tmp_path / "src" / "note.md"
    p.write_text("# 标题\n这是足够长的正文内容说明，用于 resume 验证。", encoding="utf-8")
    work = tmp_path / "work"
    # 首次带 checkpoint 跑完
    mgr = _FakeManager()
    run_file_ingestion(tmp_path / "src", _settings(), manager=mgr, work_dir=work)
    first_calls = mgr.add_calls
    # resume 再跑 → 跳过已完成文件
    mgr2 = _FakeManager()
    results = run_file_ingestion(
        tmp_path / "src", _settings(), manager=mgr2, work_dir=work, resume=True,
    )
    assert results[0].status == "skipped"
    assert results[0].reason == "checkpoint_done"
    assert mgr2.add_calls == 0
    assert first_calls >= 1


def test_run_file_ingestion_missing_dir_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        run_file_ingestion(tmp_path / "nope", _settings(), manager=_FakeManager())
