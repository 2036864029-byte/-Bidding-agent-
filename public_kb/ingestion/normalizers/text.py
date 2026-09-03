"""TextNormalizer — text 源直通 markdown IR（方案 Phase 5 / E-5）。"""

from __future__ import annotations

from typing import List

from ..ir import NormalizedDocument
from .base import Normalizer


class TextNormalizer(Normalizer):
    source_format = "text"

    def normalize(self, source: NormalizedDocument) -> List[NormalizedDocument]:
        """文本原样进 markdown IR（内容已在 E-2 router 读取；清洗在 Stage C）。"""
        return [
            NormalizedDocument(
                doc_id=source.doc_id,
                source_format="text",
                ir_format="markdown",
                content=source.content or "",
                source_path=source.source_path,
                metadata={
                    **source.metadata,
                    "parser_version": "text/v1",
                    "quality_score": 1.0,
                },
            )
        ]
