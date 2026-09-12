"""ingestion.normalizers — Stage B 规范化器（方案 Phase 5 / E-4~E-7）。"""

from __future__ import annotations

from typing import Any

from ..ir import SourceFormat
from .base import Normalizer
from .csv import CsvNormalizer
from .image import ImageOcrNormalizer
from .pdf.normalizer import PdfNormalizer
from .text import TextNormalizer

__all__ = [
    "Normalizer", "CsvNormalizer", "PdfNormalizer", "TextNormalizer",
    "ImageOcrNormalizer", "build_normalizer",
]


def build_normalizer(fmt: SourceFormat, *, settings: Any = None) -> Normalizer:
    """按来源格式构造 normalizer（pdf/image 需要 settings 供 parser/OCR 装配）。"""
    if fmt == "text":
        return TextNormalizer()
    if fmt == "csv":
        return CsvNormalizer()
    if fmt == "pdf":
        return PdfNormalizer(settings=settings)
    if fmt == "image":
        return ImageOcrNormalizer(settings=settings)
    raise ValueError(f"未知来源格式: {fmt!r}")
