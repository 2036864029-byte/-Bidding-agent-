"""Offline regression tests for consolidated CSV CLI entrypoints.

9.3 C-2：CSV 路径统一走三段式 FileIngestionPipeline（原 CsvSource/IngestionPipeline
mock 移除）；A/C 分组编排、no-import 预览、initialize→append 模式切换语义保留。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pytest

import public_kb.ingestion.cli as cli


@dataclass
class FakeSettings:
    chunk_max_chars: int = 100
    chunk_overlap_chars: int = 10


class FakeMilvusSink:
    instances: List[Any] = []

    def __init__(self, manager: Any, *, mode: str = "append") -> None:
        self.manager = manager
        self.mode = mode
        FakeMilvusSink.instances.append(self)


class FakeFilePipeline:
    instances: List[Any] = []

    def __init__(self, settings: Any, *, sink: Any = None) -> None:
        self.settings = settings
        self.sink = sink
        FakeFilePipeline.instances.append(self)

    def run(self, paths: List[str]) -> list[cli.FileIngestionResult]:
        return [
            cli.FileIngestionResult(
                path=str(p), source_format="csv", status="completed",
                chunk_count=2, inserted_count=2,
                detail={"table_row_count": 2},
            )
            for p in paths
        ]


def _file_ingestion_result() -> cli.FileIngestionResult:
    return cli.FileIngestionResult(
        path="fake.csv", source_format="csv", status="completed",
        chunk_count=2, inserted_count=2,
    )


def test_cli_dispatches_single_csv_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []

    def fake_run(
        csv_path: str,
        settings: Any,
        *,
        mode: str,
    ) -> cli.FileIngestionResult:
        calls.append(
            {
                "csv_path": csv_path,
                "settings": settings,
                "mode": mode,
            }
        )
        return _file_ingestion_result()

    monkeypatch.setattr(cli, "run_csv_ingestion", fake_run)
    exit_code = cli.main(["--csv-path", "sample.csv", "--mode", "append"])

    assert exit_code == 0
    assert calls[0]["csv_path"] == "sample.csv"
    assert calls[0]["mode"] == "append"


def test_cli_dispatches_batch_csv_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []
    result = cli.BatchCSVIngestionResult((), ())

    def fake_run(
        csv_dir: str,
        settings: Any,
        *,
        group: str | None,
        no_import: bool,
        mode: str,
    ) -> cli.BatchCSVIngestionResult:
        calls.append(
            {
                "csv_dir": csv_dir,
                "settings": settings,
                "group": group,
                "no_import": no_import,
                "mode": mode,
            }
        )
        return result

    monkeypatch.setattr(cli, "run_batch_csv_ingestion", fake_run)
    exit_code = cli.main(["--csv-dir", "raw_policy", "--group", "A", "--no-import"])

    assert exit_code == 0
    assert calls[0]["csv_dir"] == "raw_policy"
    assert calls[0]["group"] == "A"
    assert calls[0]["no_import"] is True


def test_cli_rejects_both_csv_path_and_csv_dir() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--csv-path", "sample.csv", "--csv-dir", "raw_policy"])


def test_cli_validate_only_does_not_require_csv_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview_dir = tmp_path / "preview"
    preview_dir.mkdir()
    (preview_dir / "sample_chunks.md").write_text("```text\nchunk\n```\n", encoding="utf-8")

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("validate-only must not ingest CSV")

    monkeypatch.setattr(cli, "run_csv_ingestion", fail)
    monkeypatch.setattr(cli, "run_batch_csv_ingestion", fail)

    exit_code = cli.main(["--validate-only", "--markdown-output-dir", str(preview_dir)])

    assert exit_code == 0


def test_scan_csv_files_classifies_policy_qa_and_unknown(tmp_path: Path) -> None:
    (tmp_path / "policy_data.csv").write_text("title,content\n", encoding="utf-8-sig")
    (tmp_path / "qa_data.csv").write_text("question,answer\n", encoding="utf-8-sig")
    (tmp_path / "unknown_data.csv").write_text("other,value\n", encoding="utf-8-sig")

    policy_files, qa_files, unknown_files = cli.scan_csv_files(tmp_path)

    assert [Path(path).name for path in policy_files] == ["policy_data.csv"]
    assert [Path(path).name for path in qa_files] == ["qa_data.csv"]
    assert [Path(path).name for path in unknown_files] == ["unknown_data.csv"]


def test_process_csv_group_ingests_with_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeFilePipeline.instances = []
    FakeMilvusSink.instances = []
    monkeypatch.setattr(cli, "FileIngestionPipeline", FakeFilePipeline)
    monkeypatch.setattr(cli, "MilvusSink", FakeMilvusSink)

    stats = cli.process_csv_group(
        ["a.csv", "b.csv"], "A", settings=FakeSettings(), manager={"manager": True}
    )

    assert stats["processed"] == 2
    assert stats["total_chunks"] == 4
    assert stats["total_rows"] == 4
    assert stats["imported"] is True
    assert FakeMilvusSink.instances[0].manager == {"manager": True}
    assert FakeFilePipeline.instances[0].sink is FakeMilvusSink.instances[0]


def test_process_csv_group_counts_failed_files_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingPipeline:
        instances: List[Any] = []

        def __init__(self, settings: Any, *, sink: Any = None) -> None:
            self.settings = settings
            self.sink = sink
            FailingPipeline.instances.append(self)

        def run(self, paths: List[str]):
            return [
                cli.FileIngestionResult(
                    path="bad.csv", source_format="csv",
                    status="failed", reason="broken csv",
                ),
                cli.FileIngestionResult(
                    path="good.csv", source_format="csv",
                    status="completed", chunk_count=2, inserted_count=2,
                ),
            ]

    monkeypatch.setattr(cli, "FileIngestionPipeline", FailingPipeline)

    stats = cli.process_csv_group(
        ["bad.csv", "good.csv"], "A",
        settings=FakeSettings(), manager=None, skip_import=True,
    )

    assert stats["processed"] == 1
    assert stats["failed"] == 1
    assert stats["failed_files"] == ["bad.csv"]
    assert stats["imported"] is False
    assert FailingPipeline.instances[0].sink is None  # skip_import → 预览干跑


def test_batch_csv_ingestion_does_not_create_manager_for_no_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: List[Any] = []

    def fake_scan(csv_dir: Any, *, loader: Any = None):
        return [["a.csv"], ["c.csv"], ["unknown.csv"]]

    def fake_process(
        files: List[str],
        group_label: str,
        settings: Any,
        manager: Any,
        *,
        skip_import: bool,
        mode: str,
    ) -> Dict[str, Any]:
        calls.append((group_label, manager, skip_import))
        return cli._new_group_stats(group_label, len(files))

    monkeypatch.setattr(cli, "scan_csv_files", fake_scan)
    monkeypatch.setattr(cli, "process_csv_group", fake_process)

    result = cli.run_batch_csv_ingestion(tmp_path, FakeSettings(), no_import=True)

    assert [call[0] for call in calls] == ["A", "C"]
    assert all(call[1] is None and call[2] is True for call in calls)
    assert result.total_rows == 0
    assert result.unknown_files == ("unknown.csv",)


def test_batch_csv_ingestion_uses_initialize_only_for_first_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: List[str] = []

    def fake_scan(csv_dir: Any, *, loader: Any = None):
        return [["a.csv"], ["c.csv"], []]

    def fake_process(
        files: List[str],
        group_label: str,
        settings: Any,
        manager: Any,
        *,
        skip_import: bool,
        mode: str,
    ) -> Dict[str, Any]:
        calls.append(mode)
        return {**cli._new_group_stats(group_label, len(files)), "imported": True}

    monkeypatch.setattr(cli, "scan_csv_files", fake_scan)
    monkeypatch.setattr(cli, "process_csv_group", fake_process)

    result = cli.run_batch_csv_ingestion(
        tmp_path, FakeSettings(), manager={"manager": True}, mode="initialize"
    )

    assert calls == ["initialize", "append"]
    assert all(stats["imported"] for stats in result.group_stats)
