"""StageCheckpoint + pipeline 断点续跑离线测试（方案 Phase 5 / E-10）。"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.checkpoint import STAGE_A, STAGE_C, StageCheckpoint
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


class _Sink:
    def __init__(self) -> None:
        self.writes: list[list] = []

    def write(self, documents, *, records):  # noqa: ARG001
        self.writes.append(list(documents))
        return len(documents)


def _md(tmp_path, name="a.md", text=None):
    p = tmp_path / name
    p.write_text(text or "# 标题\n这是足够长的正文内容说明。", encoding="utf-8")
    return p


# ── StageCheckpoint 单元 ───────────────────────────────────

def test_checkpoint_mark_and_artifact(tmp_path):
    cp = StageCheckpoint(tmp_path / "work")
    p = _md(tmp_path)
    assert cp.is_done(p, STAGE_C) is False
    cp.mark(p, STAGE_A)
    cp.save_artifact(p, "# 产物内容")
    assert cp.is_done(p, STAGE_A) is True
    assert cp.has_artifact(p) is True
    assert cp.load_artifact(p) == "# 产物内容"


def test_checkpoint_manifest_persists_across_reload(tmp_path):
    cp = StageCheckpoint(tmp_path / "work")
    p = _md(tmp_path)
    cp.mark(p, STAGE_C)
    reloaded = StageCheckpoint(tmp_path / "work")
    assert reloaded.is_done(p, STAGE_C) is True


def test_checkpoint_corrupt_manifest_tolerated(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "run_manifest.json").write_text("not-json{{{", encoding="utf-8")
    cp = StageCheckpoint(work)
    assert cp.is_done(tmp_path / "x.md", STAGE_C) is False  # 不抛错


# ── pipeline --resume：杀进程后续跑跳过已完成 ──────────────

def test_resume_skips_completed_file(tmp_path):
    p = _md(tmp_path)
    work = tmp_path / "work"
    # 首次运行（模拟被杀前已完成 C）→ 手动预置 checkpoint
    cp = StageCheckpoint(work)
    cp.mark(p, STAGE_C)
    sink = _Sink()
    pipe = FileIngestionPipeline(_settings(), sink=sink, checkpoint=cp, resume=True)
    res = pipe.run([p])[0]
    assert res.status == "skipped"
    assert res.reason == "checkpoint_done"
    assert sink.writes == []  # 未重新写入


def test_resume_without_flag_reprocesses(tmp_path):
    p = _md(tmp_path)
    cp = StageCheckpoint(tmp_path / "work")
    cp.mark(p, STAGE_C)
    sink = _Sink()
    pipe = FileIngestionPipeline(_settings(), sink=sink, checkpoint=cp, resume=False)
    res = pipe.run([p])[0]
    assert res.status == "completed"
    assert len(sink.writes) == 1  # 未开 resume → 重跑


# ── pipeline --rerun-from=B：复用 A 产物，不重新解析 ───────

def test_rerun_from_b_reuses_stage_a_artifact(tmp_path):
    p = _md(tmp_path, text="# 标题\n第一版足够长的正文内容说明。")
    work = tmp_path / "work"
    sink = _Sink()
    # 首次：完成 A+C，产物固化第一版
    FileIngestionPipeline(_settings(), sink=sink, checkpoint=StageCheckpoint(work)).run([p])
    # 文件内容变化（模拟重新爬取/修订）
    p.write_text("# 标题\n第二版完全不同的足够长正文内容说明。", encoding="utf-8")
    # --rerun-from=B：应复用 A 产物（第一版），不重新解析
    cp2 = StageCheckpoint(work)
    sink2 = _Sink()
    res = FileIngestionPipeline(
        _settings(), sink=sink2, checkpoint=cp2, rerun_from="B"
    ).run([p])[0]
    assert res.status == "completed"
    assert res.detail.get("stage_a_reused") is True
    docs = sink2.writes[0]
    assert any("第一版" in d.page_content for d in docs)
    assert not any("第二版" in d.page_content for d in docs)


def test_rerun_from_b_without_artifact_normalizes(tmp_path):
    p = _md(tmp_path)
    work = tmp_path / "work"
    sink = _Sink()
    res = FileIngestionPipeline(
        _settings(), sink=sink, checkpoint=StageCheckpoint(work), rerun_from="B"
    ).run([p])[0]
    assert res.status == "completed"
    assert res.detail.get("stage_a_reused") is None  # 无产物 → 正常 normalize
