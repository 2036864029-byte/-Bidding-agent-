# 功能：JSONL 行级断点续传入库。
"""JSONL 行级断点续传入库器。

按行读取分块产物（documents.jsonl），批量 embedding + Milvus 写入，
通过同目录 ``*.progress.json`` 记录已完成的行号。中断后重启自动跳过已写入行，
且 ``MilvusStoreManager`` 的 chunk_uid 去重提供二次幂等保护。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


@dataclass
class JsonlIngestResult:
    """JSONL 入库结果。"""

    jsonl_path: Path
    total_lines: int
    inserted: int
    skipped_lines: int
    progress_path: Path


def _default_progress_path(jsonl_path: Path) -> Path:
    return jsonl_path.with_suffix(".progress.json")


def _load_progress(progress_path: Path) -> int:
    """返回上次成功写入的最后一行行号（0 表示尚未开始）。"""
    if not progress_path.exists():
        return 0
    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return int(data.get("last_line", 0))
    except Exception as exc:
        logger.warning("进度文件读取失败 %s: %s，按无进度处理", progress_path, exc)
        return 0


def _save_progress(progress_path: Path, last_line: int, inserted: int) -> None:
    """原子写入进度文件。"""
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = progress_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"last_line": last_line, "inserted": inserted},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(progress_path)


def ingest_jsonl_with_resume(
    jsonl_path: str | Path,
    manager: Any,
    *,
    batch_size: int = 100,
    progress_path: Optional[str | Path] = None,
    max_workers: Optional[int] = None,
    flush: bool = True,
) -> JsonlIngestResult:
    """按行读取 JSONL，批量向量化写入 Milvus，支持断点续传。

    Args:
        jsonl_path: documents.jsonl 路径。
        manager: MilvusStoreManager 实例。
        batch_size: 每批处理的行数。
        progress_path: 进度文件路径；默认 ``{jsonl_path}.progress.json``。
        max_workers: 保留参数（实际并发度由 manager 的 embedding_max_workers 控制）。
        flush: 每批是否强制落盘；批量导入传 False 可避免逐批 flush 的秒级开销，
            由调用方在全部批次完成后统一 flush 一次。

    Returns:
        JsonlIngestResult，包含实际写入行数。
    """
    jsonl_path = Path(jsonl_path)
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL 文件不存在: {jsonl_path}")

    progress_path = Path(progress_path) if progress_path else _default_progress_path(jsonl_path)
    last_completed = _load_progress(progress_path)
    logger.info("开始入库 %s，已跳过 %d 行", jsonl_path.name, last_completed)

    total_lines = 0
    inserted_total = 0
    skipped_lines = last_completed
    batch: list[Document] = []

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            total_lines = line_no
            if line_no <= last_completed:
                continue
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("第 %d 行 JSON 解析失败，已跳过: %s", line_no, exc)
                continue
            batch.append(
                Document(
                    page_content=record.get("page_content", ""),
                    metadata=record.get("metadata") or {},
                )
            )
            if len(batch) >= batch_size:
                inserted = manager.add_documents(batch, flush=flush)
                inserted_total += inserted
                batch = []
                _save_progress(progress_path, line_no, inserted_total)
                logger.debug("进度更新: 第 %d 行, 累计写入 %d", line_no, inserted_total)

        if batch:
            inserted = manager.add_documents(batch, flush=flush)
            inserted_total += inserted
            _save_progress(progress_path, total_lines, inserted_total)
            logger.debug("进度更新: 第 %d 行, 累计写入 %d", total_lines, inserted_total)

    logger.info(
        "JSONL 入库完成 %s: 总行数 %d, 跳过 %d, 写入 %d",
        jsonl_path.name, total_lines, skipped_lines, inserted_total,
    )
    return JsonlIngestResult(
        jsonl_path=jsonl_path,
        total_lines=total_lines,
        inserted=inserted_total,
        skipped_lines=skipped_lines,
        progress_path=progress_path,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m public_kb.ingestion.sinks.jsonl_ingest_runner path [--collection NAME]."""
    import argparse

    from core.config.settings import Settings
    from core.embeddings import get_embeddings
    from core.vectorstore.milvus_client import MilvusStoreManager

    parser = argparse.ArgumentParser(description="从 documents.jsonl 断点续传入库 Milvus")
    parser.add_argument("jsonl_path", help="documents.jsonl 路径（支持通配由 shell 展开）")
    parser.add_argument("--collection", help="Milvus 集合名；默认读配置")
    parser.add_argument("--batch-size", type=int, default=100, help="每批行数")
    parser.add_argument("--progress-path", help="进度文件路径；默认 {jsonl}.progress.json")
    parser.add_argument("--defer-flush", action="store_true", help="不逐批 flush，末尾统一落盘一次（大批量入库）")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.collection:
        settings.collection_name = args.collection
    manager = MilvusStoreManager(settings, get_embeddings(settings))
    manager.load_existing()

    result = ingest_jsonl_with_resume(
        args.jsonl_path,
        manager,
        batch_size=args.batch_size,
        progress_path=args.progress_path,
        flush=not args.defer_flush,
    )
    if args.defer_flush:
        manager.collection.flush(settings.collection_name)
    print(
        f"完成: {result.jsonl_path.name} "
        f"总行数={result.total_lines} 写入={result.inserted} "
        f"进度={result.progress_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
