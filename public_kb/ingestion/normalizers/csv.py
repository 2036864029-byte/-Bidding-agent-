"""CsvNormalizer — csv 双 IR 输出（方案 Phase 5 / E-6）。

一个 csv 源产出两类 IR：
  1. ``ir_format="table"`` —— 表摘要（列清单 + 采样行 markdown 表），
     供结构路（price 侧 SQL 描述/消费）使用；全列全行保留于 metadata。
  2. ``ir_format="markdown"`` —— 行块语义（政策/QA 类 csv 每行 → 可读文本块），
     供向量路（Stage C 清洗后按行块切分）使用；结构化数据 csv（无 content/QA
     列）该路为空，仅产出 table IR。
"""

from __future__ import annotations

import csv
import logging
from typing import List

from ..csv_loader import CsvLoader
from ..ir import NormalizedDocument
from .base import Normalizer

logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_ROWS = 20


def _read_all_rows(path: str) -> tuple[list[str], list[dict[str, str]]]:
    """通用读取：全列全行保留（不丢无 content 的结构化行）。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = [c for c in (reader.fieldnames or [])]
        rows = [dict(r) for r in reader]
    return fieldnames, rows


def _build_table_summary(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    sample_rows: int,
) -> str:
    """列清单 + 采样行 → markdown 表（表摘要）。"""
    if not fieldnames:
        return ""
    lines = ["| " + " | ".join(fieldnames) + " |", "|" + "---|" * len(fieldnames)]
    for row in rows[:sample_rows]:
        cells = [str(row.get(c, "")).replace("\n", " ").replace("|", "\\|") for c in fieldnames]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


class CsvNormalizer(Normalizer):
    source_format = "csv"

    def __init__(self, *, sample_rows: int = _DEFAULT_SAMPLE_ROWS) -> None:
        self._sample_rows = sample_rows
        self._loader = CsvLoader()

    def normalize(self, source: NormalizedDocument) -> List[NormalizedDocument]:
        path = source.source_path
        if not path:
            return []

        outputs: List[NormalizedDocument] = []

        # ── IR 1：table 摘要（结构路 / price SQL 消费） ──
        try:
            fieldnames, rows = _read_all_rows(path)
        except OSError as exc:
            logger.warning("csv 读取失败 %s: %s", path, exc)
            return outputs
        if fieldnames:
            table_doc = NormalizedDocument(
                doc_id=f"{source.doc_id}#table",
                source_format="csv",
                ir_format="table",
                content=_build_table_summary(fieldnames, rows, self._sample_rows),
                source_path=path,
                metadata={
                    **source.metadata,
                    "columns": fieldnames,
                    "row_count": len(rows),
                    "truncated": len(rows) > self._sample_rows,
                    "parser_version": "csv/table-v1",
                    "quality_score": 1.0,
                },
            )
            outputs.append(table_doc)

        # ── IR 2：行块语义（向量路，政策/QA 类） ──
        policy_rows = self._loader._parse_csv(path)  # noqa: SLF001  # 同包复用解析
        if policy_rows:
            blocks: list[str] = []
            for row in policy_rows:
                title = str(row.get("title") or row.get("source_file", "")).strip()
                content = str(row.get("content", "")).strip()
                if not content:
                    continue
                header = f"# {title}" if title else ""
                blocks.append((header + "\n" + content) if header else content)
            if blocks:
                md_doc = NormalizedDocument(
                    doc_id=f"{source.doc_id}#rows",
                    source_format="csv",
                    ir_format="markdown",
                    content="\n\n".join(blocks),
                    source_path=path,
                    metadata={
                        **source.metadata,
                        "row_count": len(blocks),
                        "parser_version": "csv/rows-v1",
                        "quality_score": 1.0,
                    },
                )
                outputs.append(md_doc)

        return outputs
