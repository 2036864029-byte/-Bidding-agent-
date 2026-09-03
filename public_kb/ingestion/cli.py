# 功能：CSV 单文件和批量向量化入库命令行入口。
"""Command-line entries for single-file and batch CSV ingestion."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..config import Settings
from .csv_loader import CsvLoader
from ..services.embeddings import create_embeddings
from ..services.milvus_store import MilvusStoreManager
from .models import IngestionResult
from ._legacy.sources.document_source import DocumentSource
from ._legacy.pipeline_legacy import IngestionPipeline
from .pipeline import FileIngestionPipeline, FileIngestionResult
from .checkpoint import StageCheckpoint
from .ledger import IngestLedger
from .quality import QualityGate
from .router import FormatRouter
from .sinks.markdown_sink import MarkdownSink
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from ._legacy.sources.csv_source import CsvSource


logger = logging.getLogger(__name__)

DEFAULT_MARKDOWN_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "DATA" / "raw_data"
DEFAULT_OUTPUT_DIR = DEFAULT_MARKDOWN_OUTPUT_DIR


def run_file_ingestion(
    files_dir: str | Path,
    settings: Settings,
    *,
    manager: Any = None,
    embeddings: Any = None,
    ledger_path: str | Path | None = None,
    work_dir: str | Path | None = None,
    resume: bool = False,
    rerun_from: str | None = None,
    quality_threshold: float | None = None,
) -> list[FileIngestionResult]:
    """三段式文件入库（P5 E-12）：路由 → 规范化 → 清洗/分块 → Milvus。

    Args:
        files_dir: 源文件目录（递归收集 text/csv/pdf）。
        manager: 注入 MilvusStoreManager（测试用）；缺省惰性构建。
        ledger_path: 文档级去重登记（缺省不启用 ledger）。
        work_dir: checkpoint 工作目录（缺省不启用断点）。
        resume: --resume，跳过 C 已完成文件。
        rerun_from: --rerun-from=B，复用 A 产物重做 B+C。
        quality_threshold: --quality-threshold，开启质量关卡（默认关闭）。

    Returns:
        每个文件的 FileIngestionResult 列表。
    """
    src_dir = Path(files_dir)
    if not src_dir.exists():
        raise FileNotFoundError(f"文件目录不存在: {src_dir}")
    files = sorted(
        p for p in src_dir.rglob("*")
        if p.is_file() and not p.name.startswith(".") and not p.name.startswith("_")
    )
    if not files:
        raise FileNotFoundError(f"目录中未找到可处理文件: {src_dir}")

    resolved_manager = manager
    if resolved_manager is None:
        resolved_embeddings = embeddings or create_embeddings(settings)
        resolved_manager = MilvusStoreManager(settings, resolved_embeddings)
    sink = MilvusSink(resolved_manager, mode="append")

    ledger = IngestLedger(ledger_path) if ledger_path else None
    checkpoint = StageCheckpoint(work_dir) if work_dir else None
    quality_gate = (
        QualityGate(
            threshold=quality_threshold,
            review_dir=(Path(work_dir) / "review") if work_dir else None,
        )
        if quality_threshold is not None
        else None
    )

    pipeline = FileIngestionPipeline(
        settings,
        sink=sink,
        ledger=ledger,
        checkpoint=checkpoint,
        resume=resume,
        rerun_from=rerun_from,
        quality_gate=quality_gate,
    )
    results = pipeline.run(files)
    _report_file_results(results)
    return results


def _report_file_results(results: list[FileIngestionResult]) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    logger.info(
        "文件入库汇总: %s | chunks=%d inserted=%d",
        ", ".join(f"{k}={v}" for k, v in counts.items()),
        sum(r.chunk_count for r in results),
        sum(r.inserted_count for r in results),
    )
    for r in results:
        if r.status in ("skipped", "failed"):
            logger.info("  [%s] %s (%s)", r.status, r.path, r.reason)


@dataclass(frozen=True)
class BatchCSVIngestionResult:
    """Aggregated result for one batch CSV invocation."""

    group_stats: Tuple[Dict[str, object], ...]
    unknown_files: Tuple[str, ...]

    @property
    def total_rows(self) -> int:
        return sum(int(stat.get("total_rows", 0)) for stat in self.group_stats)

    @property
    def total_chunks(self) -> int:
        return sum(int(stat.get("total_chunks", 0)) for stat in self.group_stats)


def run_csv_ingestion(
    csv_path: str,
    settings: Settings,
    *,
    markdown_output_dir: str | None = None,
    manager: MilvusStoreManager | None = None,
    embeddings: Any = None,
    mode: MilvusSinkMode = "append",
) -> IngestionResult:
    """Load one CSV directly and write it to Milvus without Markdown round-trip."""
    resolved_embeddings = embeddings
    resolved_manager = manager
    if resolved_manager is None:
        resolved_embeddings = resolved_embeddings or create_embeddings(settings)
        resolved_manager = MilvusStoreManager(
            settings,
            resolved_embeddings,
        )
    loader = CsvLoader(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    sinks = []
    if markdown_output_dir:
        sinks.append(MarkdownSink(markdown_output_dir))
    sinks.append(MilvusSink(resolved_manager, mode=mode))

    return IngestionPipeline(sinks).run(CsvSource(csv_path, loader=loader))


def scan_csv_files(
    csv_dir: str | Path,
    *,
    loader: CsvLoader | None = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Scan ``*_data.csv`` files and classify them as policy or QA groups."""
    csv_path = Path(csv_dir)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 目录不存在: {csv_path}")

    csv_files = sorted(csv_path.glob("*_data.csv"))
    if not csv_files:
        raise FileNotFoundError(f"目录中未找到 *_data.csv 文件: {csv_path}")

    csv_loader = loader or CsvLoader()
    policy_files: List[str] = []
    qa_files: List[str] = []
    unknown_files: List[str] = []
    for csv_file in csv_files:
        category = csv_loader.classify_file(str(csv_file))
        if category == "A":
            policy_files.append(str(csv_file))
        elif category == "C":
            qa_files.append(str(csv_file))
        else:
            unknown_files.append(str(csv_file))
    return policy_files, qa_files, unknown_files


def _new_group_stats(group_label: str, file_count: int) -> Dict[str, object]:
    return {
        "group": group_label,
        "total_files": file_count,
        "processed": 0,
        "failed": 0,
        "total_rows": 0,
        "total_chunks": 0,
        "failed_files": [],
        "imported": False,
    }


def process_csv_group(
    files: List[str],
    group_label: str,
    loader: CsvLoader,
    manager: Any,
    output_dir: str,
    *,
    skip_import: bool = False,
    mode: MilvusSinkMode = "append",
) -> Dict[str, object]:
    """Preview and ingest one classified group of CSV files."""
    stats = _new_group_stats(group_label, len(files))
    failed_files = stats["failed_files"]
    assert isinstance(failed_files, list)
    if not files:
        return stats

    if not skip_import and manager is None:
        raise ValueError("manager is required unless skip_import is true")

    all_documents = []
    for file_path in files:
        file_name = Path(file_path).name
        try:
            source_result = CsvSource(file_path, loader=loader).load()
            documents = source_result.documents
            records = source_result.records
            if not documents:
                stats["processed"] = int(stats["processed"]) + 1
                continue

            all_documents.extend(documents)
            stats["processed"] = int(stats["processed"]) + 1
            stats["total_rows"] = int(stats["total_rows"]) + len(records)
            stats["total_chunks"] = int(stats["total_chunks"]) + len(documents)
            MarkdownSink(output_dir, source_file=file_name).write(
                documents,
                records=records,
            )
        except Exception as error:
            stats["failed"] = int(stats["failed"]) + 1
            failed_files.append(file_name)
            logger.error("处理 %s 失败: %s", file_name, error)

    if skip_import:
        logger.info("已跳过入库（--no-import）")
        return stats
    if not all_documents:
        return stats

    try:
        ingestion_result = IngestionPipeline(
            [MilvusSink(manager, mode=mode)]
        ).run(
            DocumentSource(
                all_documents,
                source_name=f"csv_group_{group_label}",
            )
        )
        stats["imported"] = True
        logger.info(
            "组 %s 入库完成: chunks=%d, inserted=%d",
            group_label,
            ingestion_result.chunk_count,
            ingestion_result.inserted_count,
        )
    except Exception as error:
        logger.error("组 %s 入库失败: %s", group_label, error)
    return stats


def validate_markdown_output(output_dir: str | Path) -> Dict[str, int]:
    """Count preview chunks in each generated Markdown file."""
    result: Dict[str, int] = {}
    markdown_files = sorted(Path(output_dir).glob("*_chunks.md"))
    for markdown_file in markdown_files:
        try:
            content = markdown_file.read_text(encoding="utf-8")
            result[markdown_file.name] = content.count("```") // 2
        except Exception as error:
            logger.warning("校验 %s 失败: %s", markdown_file.name, error)
            result[markdown_file.name] = -1
    return result


def run_batch_csv_ingestion(
    csv_dir: str | Path,
    settings: Settings,
    *,
    group: str | None = None,
    no_import: bool = False,
    markdown_output_dir: str | None = None,
    manager: Any = None,
    embeddings: Any = None,
    mode: MilvusSinkMode = "append",
) -> BatchCSVIngestionResult:
    """Scan, preview, and ingest classified CSV files from one directory."""
    if group not in {None, "A", "C"}:
        raise ValueError("group 必须是 A、C 或 None")

    policy_files, qa_files, unknown_files = scan_csv_files(csv_dir)
    selected_groups = []
    if group is None or group == "A":
        selected_groups.append(("A", policy_files))
    if group is None or group == "C":
        selected_groups.append(("C", qa_files))

    resolved_output_dir = str(
        Path(markdown_output_dir or DEFAULT_MARKDOWN_OUTPUT_DIR)
    )
    Path(resolved_output_dir).mkdir(parents=True, exist_ok=True)

    if no_import:
        resolved_manager = None
    else:
        resolved_embeddings = embeddings
        resolved_manager = manager
        if resolved_manager is None:
            resolved_embeddings = resolved_embeddings or create_embeddings(settings)
            resolved_manager = MilvusStoreManager(
                settings,
                resolved_embeddings,
            )

    pending_mode = mode
    group_stats: List[Dict[str, object]] = []
    for group_label, files in selected_groups:
        stats = process_csv_group(
            files,
            group_label,
            CsvLoader(
                max_chars=settings.chunk_max_chars,
                overlap_chars=settings.chunk_overlap_chars,
            ),
            resolved_manager,
            resolved_output_dir,
            skip_import=no_import,
            mode=pending_mode,
        )
        group_stats.append(stats)
        if pending_mode == "initialize" and bool(stats.get("imported")):
            pending_mode = "append"
    return BatchCSVIngestionResult(tuple(group_stats), tuple(unknown_files))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest files (three-stage) or legacy CSV into Milvus")
    path_group = parser.add_mutually_exclusive_group(required=False)
    path_group.add_argument("--files", help="三段式：源文件目录（递归 text/csv/pdf）")
    path_group.add_argument("--csv-path", help="（legacy）单个 CSV 文件路径")
    path_group.add_argument("--csv-dir", help="（legacy）包含 *_data.csv 的目录")
    parser.add_argument(
        "--markdown-output-dir",
        "--output-dir",
        dest="markdown_output_dir",
        help="Markdown 预览输出目录；批量模式默认写入 DATA/raw_data",
    )
    parser.add_argument(
        "--mode",
        choices=("initialize", "append"),
        default="append",
    )
    parser.add_argument("--group", choices=("A", "C"), help="（legacy）仅处理指定批量分组")
    parser.add_argument("--no-import", action="store_true", help="只生成预览，不写入 Milvus")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验已有 Markdown 预览，不处理 CSV",
    )
    # ── 三段式（--files）专用开关（P5 E-12） ──
    parser.add_argument(
        "--legacy", action="store_true",
        help="回滚：对 CSV 文件走旧 Source 流水线（pipeline_legacy），非 CSV 跳过",
    )
    parser.add_argument("--ledger", help="文档级去重登记文件（ingest_ledger.jsonl）")
    parser.add_argument("--work-dir", help="checkpoint 工作目录（断点续跑）")
    parser.add_argument("--resume", action="store_true", help="跳过 checkpoint 中已完成的文件")
    parser.add_argument(
        "--rerun-from", choices=("B",), help="复用 Stage A 中间产物，重做 B+C",
    )
    parser.add_argument(
        "--quality-threshold", type=float, default=None,
        help="质量关卡阈值（默认关闭；低于阈值旁路 review_queue）",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    markdown_output_dir = args.markdown_output_dir

    if not args.validate_only and not args.files and not args.csv_path and not args.csv_dir:
        parser.error("必须提供 --files、--csv-path 或 --csv-dir")

    if args.validate_only:
        output_dir = markdown_output_dir or DEFAULT_MARKDOWN_OUTPUT_DIR
        validation = validate_markdown_output(output_dir)
        logger.info("校验完成: %d 个文件, 共 %d chunks", len(validation), sum(validation.values()))
        return 0

    settings = Settings()

    # ── 三段式文件入库（P5 E-12） ──
    if args.files:
        if args.legacy:
            # 回滚：仅 CSV 走旧流水线
            policy_files, qa_files, unknown = scan_csv_files(args.files)
            result = run_batch_csv_ingestion(
                args.files,
                settings,
                no_import=args.no_import,
                markdown_output_dir=markdown_output_dir,
                mode=args.mode,
            )
            logger.info(
                "legacy 回滚: 政策=%d QA=%d 未知=%d；结果见上",
                len(policy_files), len(qa_files), len(unknown),
            )
            return 0
        run_file_ingestion(
            args.files,
            settings,
            ledger_path=args.ledger,
            work_dir=args.work_dir,
            resume=args.resume,
            rerun_from=args.rerun_from,
            quality_threshold=args.quality_threshold,
        )
        return 0

    # ── legacy CSV 路径（原行为不变） ──
    if args.csv_path:
        result = run_csv_ingestion(
            args.csv_path,
            settings,
            markdown_output_dir=markdown_output_dir,
            mode=args.mode,
        )
        logger.info(
            "CSV ingestion %s: chunks=%d, inserted=%d",
            result.status,
            result.chunk_count,
            result.inserted_count,
        )
    else:
        result = run_batch_csv_ingestion(
            args.csv_dir,
            settings,
            group=args.group,
            no_import=args.no_import,
            markdown_output_dir=markdown_output_dir,
            mode=args.mode,
        )
        logger.info("未知文件: %d 个", len(result.unknown_files))
        for stats in result.group_stats:
            logger.info(
                "组 %s: %d/%d 成功, %d 行, %d chunks, 入库=%s",
                stats.get("group"),
                stats.get("processed"),
                stats.get("total_files"),
                stats.get("total_rows"),
                stats.get("total_chunks"),
                stats.get("imported"),
            )
        logger.info("合计: %d 行, %d chunks", result.total_rows, result.total_chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
