"""JSONL 行级断点续传入库测试（离线，不连 Milvus）。"""

from __future__ import annotations

import json
from pathlib import Path

from public_kb.ingestion.sinks.jsonl_ingest_runner import (
    _load_progress,
    ingest_jsonl_with_resume,
)


def _write_jsonl(path: Path, count: int = 5) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({
                "page_content": f"内容第{i}段。",
                "metadata": {
                    "doc_name": "demo",
                    "chapter": f"第{i}章",
                    "chunk_index": i,
                },
            }, ensure_ascii=False) + "\n")


class FakeManager:
    """记录每次 add_documents 的文档并返回写入数。"""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.added: list = []
        self.call_count = 0
        self.fail_on_call = fail_on_call

    def add_documents(self, documents, *, flush: bool = True) -> int:
        self.call_count += 1
        if self.fail_on_call == self.call_count:
            raise RuntimeError("模拟中断")
        self.added.extend(documents)
        return len(documents)


def test_fresh_run_writes_all_and_creates_progress(tmp_path):
    path = tmp_path / "demo.documents.jsonl"
    _write_jsonl(path, count=5)
    manager = FakeManager()
    result = ingest_jsonl_with_resume(path, manager, batch_size=2)
    assert result.inserted == 5
    assert len(manager.added) == 5
    assert result.progress_path.exists()
    assert _load_progress(result.progress_path) == 5


def test_rerun_skips_completed_lines(tmp_path):
    path = tmp_path / "demo.documents.jsonl"
    _write_jsonl(path, count=5)
    first = FakeManager()
    ingest_jsonl_with_resume(path, first, batch_size=2)

    second = FakeManager()
    result = ingest_jsonl_with_resume(path, second, batch_size=2)
    assert result.inserted == 0
    assert second.added == []
    assert result.skipped_lines == 5


def test_resume_skips_preseeded_progress(tmp_path):
    path = tmp_path / "demo.documents.jsonl"
    _write_jsonl(path, count=5)
    progress = path.with_suffix(".progress.json")
    progress.write_text(json.dumps({"last_line": 3, "inserted": 3}), encoding="utf-8")

    manager = FakeManager()
    result = ingest_jsonl_with_resume(path, manager, batch_size=100)
    assert result.skipped_lines == 3
    assert result.inserted == 2
    assert len(manager.added) == 2
    assert manager.added[0].page_content == "内容第3段。"


def test_crash_resume_continues_from_last_committed_batch(tmp_path):
    path = tmp_path / "demo.documents.jsonl"
    _write_jsonl(path, count=5)

    # 第一次：第 2 次 add_documents 时中断（batch_size=2 → 已提交前 2 行）
    crash = FakeManager(fail_on_call=2)
    try:
        ingest_jsonl_with_resume(path, crash, batch_size=2)
    except RuntimeError:
        pass
    progress = path.with_suffix(".progress.json")
    assert _load_progress(progress) == 2
    assert len(crash.added) == 2

    # 重启：跳过已提交的 2 行，继续剩余 3 行
    resume = FakeManager()
    result = ingest_jsonl_with_resume(path, resume, batch_size=2)
    assert result.inserted == 3
    assert len(resume.added) == 3
    assert resume.added[0].page_content == "内容第2段。"
    assert _load_progress(progress) == 5
