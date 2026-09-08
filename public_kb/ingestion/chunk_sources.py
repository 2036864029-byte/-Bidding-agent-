# 功能：对已清洗 Markdown 目录做类型化分块，落盘 documents.jsonl。
"""分块落盘脚本（Phase 3）。

消费两类已清洗 Markdown 中间态：
  - cleaned_v1/markdown：349 部法规（doc_type=law）
  - handoff_v3/clean：实务书 / 问答 / 法规全书（doc_type 按文件名识别）

只做分块 + 元数据 + chunk_uid 固化，**不调 embedding、不写 Milvus**，
产出可审计、可重跑的分块资产，供断点续传入库器消费。

用法：
    python -m public_kb.ingestion.chunk_sources \
        --law-dir DATA/raw_data/cleaned_v1/markdown \
        --book-dir DATA/raw_data/handoff_v3/clean \
        --out-dir DATA/work/chunks
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.config.settings import Settings

from .handoff import dump_documents_jsonl
from .transforms import DocType, TypedChunker, detect_doc_type

logger = logging.getLogger(__name__)

_ASSEMBLED_SUFFIX = ".assembled"

_DOC_ID_PREFIX = {
    DocType.LAW: "law",
    DocType.LAW_COMPILATION: "lawcomp",
    DocType.QA: "qa",
    DocType.BOOK_PRACTICAL: "book",
}


@dataclass
class SourceChunkResult:
    """单个目录分块结果。"""

    source_dir: str
    files: int
    total_chunks: int
    failed: List[str]


def _doc_name_from_path(path: Path) -> str:
    """从文件名推导 doc_name（去掉 .assembled 后缀）。"""
    name = path.stem
    if name.endswith(_ASSEMBLED_SUFFIX):
        name = name[: -len(_ASSEMBLED_SUFFIX)]
    return name


def _doc_id(doc_type: DocType, seq: int) -> str:
    return f"{_DOC_ID_PREFIX.get(doc_type, 'doc')}_{seq:03d}"


def chunk_directory(
    src_dir: str | Path,
    settings: Settings,
    *,
    out_dir: str | Path,
    force_doc_type: Optional[DocType | str] = None,
    pattern: str = "*.md",
) -> SourceChunkResult:
    """切分一个目录下所有 Markdown，每文件产出一个 ``{doc_id}.documents.jsonl``。"""
    src = Path(src_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"源目录不存在: {src}")

    files = sorted(src.glob(pattern))
    files = [f for f in files if not f.name.startswith("_")]
    if not files:
        raise FileNotFoundError(f"目录中未找到 {pattern} 文件: {src}")

    chunker = TypedChunker(settings)
    total_chunks = 0
    failed: List[str] = []

    for seq, md_file in enumerate(files, start=1):
        doc_name = _doc_name_from_path(md_file)
        doc_type = (
            force_doc_type
            if force_doc_type is not None
            else detect_doc_type(md_file)
        )
        if isinstance(doc_type, str):
            doc_type = DocType(doc_type)
        try:
            docs = chunker.chunk(
                md_file.read_text(encoding="utf-8"),
                doc_name,
                doc_type,
                doc_id=_doc_id(doc_type, seq),
                source_format="text",
            )
            out_file = out / f"{_doc_id(doc_type, seq)}.documents.jsonl"
            dump_documents_jsonl(docs, out_file)
            total_chunks += len(docs)
            logger.info("✓ %s → %s（%s 块）", md_file.name, out_file.name, len(docs))
        except Exception as exc:  # noqa: BLE001
            failed.append(md_file.name)
            logger.error("✗ %s 分块失败: %s", md_file.name, exc)

    logger.info(
        "目录 %s 分块完成: %d 文件, %d 块, 失败 %d",
        src, len(files), total_chunks, len(failed),
    )
    return SourceChunkResult(
        source_dir=str(src),
        files=len(files),
        total_chunks=total_chunks,
        failed=failed,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="对已清洗 Markdown 做类型化分块并落盘 documents.jsonl"
    )
    parser.add_argument("--law-dir", help="法规目录（cleaned_v1/markdown）")
    parser.add_argument("--book-dir", help="书籍/问答目录（handoff_v3/clean）")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="输出目录（每文件一个 *.documents.jsonl）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    out = Path(args.out_dir)

    results: List[SourceChunkResult] = []
    if args.law_dir:
        results.append(chunk_directory(
            args.law_dir, settings, out_dir=out,
            force_doc_type=DocType.LAW,
        ))
    if args.book_dir:
        results.append(chunk_directory(
            args.book_dir, settings, out_dir=out,
        ))

    if not results:
        print("请提供 --law-dir 或 --book-dir 至少一个")
        return 1

    total_chunks = sum(r.total_chunks for r in results)
    total_failed = sum(len(r.failed) for r in results)
    print(f"分块完成: 共 {total_chunks} 块, 失败 {total_failed}")
    for r in results:
        print(f"  {r.source_dir}: {r.files} 文件, {r.total_chunks} 块")
        for name in r.failed:
            print(f"    ✗ {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
