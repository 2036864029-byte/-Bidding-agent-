"""FormatRouter — Stage A：来源格式分发（方案 Phase 5 / E-2）。

扩展名 + 内容嗅探把源文件分到 text / csv / pdf 三类 SourceFormat；
text 类直接产出 markdown IR（读文件即可），csv/pdf 的完整规范化由
normalizers/{csv,pdf} 完成（E-6/E-7）。

纯本地文件操作，无网络/模型调用。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from .ir import NormalizedDocument, SourceFormat, compute_source_hash

logger = logging.getLogger(__name__)

_EXT_TO_FORMAT: dict[str, SourceFormat] = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}

# 内容嗅探：无扩展名或扩展名不可信时的兜底
_SNIFF_SAMPLE_BYTES = 2048


def _sniff_format(path: Path) -> Optional[SourceFormat]:
    """读文件头部嗅探格式（优先级 pdf > csv > text）。"""
    try:
        head = path.read_bytes()[:_SNIFF_SAMPLE_BYTES]
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    # CSV 判据：头部含逗号分隔的多列（保守：至少 1 个逗号且非纯 JSON/二进制）
    try:
        head_text = head.decode("utf-8", errors="ignore")
        first_line = head_text.splitlines()[0] if head_text.splitlines() else ""
        if "," in first_line and "\x00" not in head_text:
            return "csv"
    except Exception:  # noqa: BLE001
        pass
    return "text"


class FormatRouter:
    """来源格式分发：按扩展名 → 内容嗅探 → 默认 text。"""

    def classify(self, path: str | Path) -> SourceFormat:
        """返回源文件格式。"""
        p = Path(path)
        fmt = _EXT_TO_FORMAT.get(p.suffix.lower())
        if fmt is not None:
            return fmt
        sniffed = _sniff_format(p)
        return sniffed or "text"

    def route(self, path: str | Path) -> NormalizedDocument:
        """对 text/markdown 源直接产出 NormalizedDocument；csv/pdf 需过 normalizer。

        csv/pdf 返回的 NormalizedDocument.content 为空、ir_format 置其类型，
        由 E-6/E-7 normalizer 填充分块产物。
        """
        p = Path(path).resolve()
        fmt = self.classify(p)
        doc_id = str(p)
        if fmt in ("text",):
            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = p.read_text(encoding="utf-8", errors="replace")
            return NormalizedDocument(
                doc_id=doc_id,
                source_format=fmt,
                ir_format="markdown",
                content=content,
                source_path=str(p),
                metadata={
                    "source_hash": compute_source_hash(p),
                    "parser_version": "text/v1",
                    "quality_score": 1.0,
                },
            )
        return NormalizedDocument(
            doc_id=doc_id,
            source_format=fmt,
            ir_format="table" if fmt == "csv" else "markdown",
            content="",
            source_path=str(p),
            metadata={
                "source_hash": compute_source_hash(p),
                "parser_version": "",
                "quality_score": 0.0,  # 未规范化，由 normalizer 重写
            },
        )
