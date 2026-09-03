"""ingestion.normalizers — Stage B 规范化器（方案 Phase 5 / E-4~E-7）。"""

from __future__ import annotations

from ..ir import SourceFormat
from .base import Normalizer
from .csv import CsvNormalizer
from .text import TextNormalizer

__all__ = ["Normalizer", "CsvNormalizer", "TextNormalizer", "build_normalizer"]


def build_normalizer(fmt: SourceFormat) -> Normalizer:
    """按来源格式构造 normalizer（pdf 在 E-7 收敛后接入）。"""
    if fmt == "text":
        return TextNormalizer()
    if fmt == "csv":
        return CsvNormalizer()
    if fmt == "pdf":
        raise NotImplementedError(
            "PdfNormalizer 待 E-7（transforms/pdf_* 收敛到 normalizers/pdf/）接入"
        )
    raise ValueError(f"未知来源格式: {fmt!r}")
