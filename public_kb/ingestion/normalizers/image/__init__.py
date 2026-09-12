"""ingestion.normalizers.image — 图片 OCR normalizer（RapidOCR，本地）。"""

from __future__ import annotations

from .image import ImageOcrNormalizer, _get_engine, _rows_to_markdown

__all__ = ["ImageOcrNormalizer", "_get_engine", "_rows_to_markdown"]
