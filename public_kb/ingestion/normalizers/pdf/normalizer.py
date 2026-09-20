"""pdf 归一化入口 — 解析产物 → markdown IR（方案 Phase 5 / E-7）。

9 个 pdf_* 解析模块已 git mv 收敛于此（三档路由/复杂页/Markdown 装配等）；
本入口把整本 PDF 经 parser（PdfRouter / MinerU）解析为整篇 markdown IR，
清洗与分块在 Stage C 完成（与 handoff.chunk_markdown 口径一致）。
"""

from __future__ import annotations

from typing import Any, List

from ...ir import NormalizedDocument
from ..base import Normalizer


class PdfNormalizer(Normalizer):
    source_format = "pdf"

    def __init__(self, settings: Any = None, parser: Any = None) -> None:
        """settings 供 parser_factory 建 parser；parser 可直接注入（测试用）。"""
        self._settings = settings
        self._parser = parser

    def _get_parser(self) -> Any:
        if self._parser is not None:
            return self._parser
        from ...parser_factory import build_pdf_parser

        if self._settings is None:
            from core.config.settings import Settings

            self._settings = Settings()
        return build_pdf_parser(self._settings)

    def normalize(self, source: NormalizedDocument) -> List[NormalizedDocument]:
        path = source.source_path
        if not path:
            return []
        raw_markdown = self._get_parser().parse(path)
        return [
            NormalizedDocument(
                doc_id=f"{source.doc_id}#markdown",
                source_format="pdf",
                ir_format="markdown",
                content=raw_markdown or "",
                source_path=path,
                metadata={
                    **source.metadata,
                    "parser_version": "pdf/v1",
                    "quality_score": 1.0,
                },
            )
        ]
