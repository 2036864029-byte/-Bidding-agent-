"""QualityGate — quality_score 质量关卡（方案 Phase 5 / E-11）。

Normalizer 在 IR metadata 写入 quality_score（约定见 normalizers/base.py）：
低于阈值（默认 0.8）的 markdown IR 不进向量路，旁路输出到 review_queue/ 供
人工复核，不直接入库。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

from .ir import NormalizedDocument

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.8


class QualityGate:
    """按 quality_score 分流：达标入库 / 不达标旁路 review。"""

    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        review_dir: str | Path | None = None,
    ) -> None:
        self._threshold = threshold
        self._review_dir = Path(review_dir) if review_dir else None

    @property
    def threshold(self) -> float:
        return self._threshold

    def should_review(self, quality_score: float | None) -> bool:
        """quality_score 缺失视为达标（无质量问题信号）。"""
        if quality_score is None:
            return False
        return quality_score < self._threshold

    def partition(
        self, irs: List[NormalizedDocument]
    ) -> Tuple[List[NormalizedDocument], List[NormalizedDocument]]:
        """返回 (达标 IR, 待复核 IR)。"""
        ingest, review = [], []
        for ir in irs:
            (review if self.should_review(ir.metadata.get("quality_score")) else ingest).append(ir)
        return ingest, review

    def dump_review(self, ir: NormalizedDocument, source_path: str | Path) -> Path:
        """把待复核 IR 落盘到 review_queue/（不入库）。"""
        if self._review_dir is None:
            return Path("")
        self._review_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(ir.doc_id).stem or Path(source_path).stem or "review"
        out = self._review_dir / f"{stem}.review.md"
        header = (
            f"<!-- review: doc_id={ir.doc_id} quality={ir.metadata.get('quality_score')} "
            f"parser={ir.metadata.get('parser_version')} -->\n"
        )
        out.write_text(header + ir.content, encoding="utf-8")
        logger.warning("[QUALITY_REVIEW] %s 质量 %.2f 低于阈值 %.2f → %s",
                       ir.doc_id, ir.metadata.get("quality_score", 0.0), self._threshold, out)
        return out
