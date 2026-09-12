"""图片 OCR normalizer — 图片 → Markdown（RapidOCR，本地推理，数据不出服务端）。

方案（用户决策：本地 OCR，敏感场景数据不出环境）：
  - 推理引擎 `rapidocr-onnxruntime`（**PaddleOCR 的模型 + onnxruntime 推理**，
    Windows 有预编译 wheel；与 ragas 钉死的 langchain-community 不冲突）。
  - 进程内**懒加载单例**（模型 ~几十 MB，避免每次上传重载）。
  - 识别行按 y 坐标排序、间距断段 → Markdown。
  - **引擎不可用/识别失败 → 降级产出**（quality_score<0.8），不抛错阻断上传链路；
    空识别同样降级标记（可能是非文字图片，如实告知，不产出乱码）。

启用开关：`Settings.image_ocr_enabled`（默认 true）；关闭时 normalizer 直接降级返回。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from ...ir import NormalizedDocument
from ..base import Normalizer

logger = logging.getLogger(__name__)

_ENGINE: Any = None
_ENGINE_ERROR: Optional[Exception] = None


def _get_engine() -> Tuple[Any, Optional[Exception]]:
    """懒加载 RapidOCR 单例；返回 (engine, error)。"""
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is not None or _ENGINE_ERROR is not None:
        return _ENGINE, _ENGINE_ERROR
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    except Exception as exc:  # noqa: BLE001  依赖缺失/模型加载失败 → 记住，不反复尝试
        _ENGINE_ERROR = exc
        logger.error("RapidOCR 初始化失败（图片 OCR 降级为不可用）: %s", exc)
    return _ENGINE, _ENGINE_ERROR


def _rows_to_markdown(rows: List[Any]) -> str:
    """识别行 → 按 y 排序 + 段距断行 → Markdown 文本。

    rows 每项 = [box(4 点 xy), text, score]；box 左上角 y 决定行序。
    """
    if not rows:
        return ""
    # box 左上 y 排序（容忍多列：同 y 多列暂不横向合并，逐行输出）
    rows = sorted(rows, key=lambda r: (r[0][0][1] if isinstance(r[0][0], (list, tuple)) else 0))
    lines: List[str] = []
    prev_bottom: Optional[float] = None
    for box, text, _score in rows:
        if not isinstance(text, str) or not text.strip():
            continue
        tops = [p[1] for p in box] if box else []
        bottoms = [p[1] for p in box] if box else []
        top = min(tops) if tops else 0
        bottom = max(bottoms) if bottoms else top
        if prev_bottom is not None and top - prev_bottom > 14:  # 段距阈值
            lines.append("")
        lines.append(text.strip())
        prev_bottom = max(prev_bottom or 0, bottom)
    return "\n".join(lines)


def _degraded(source: NormalizedDocument, content: str, reason: str, score: float) -> List[NormalizedDocument]:
    """降级产出：OCR 不可用/失败时如实标记，不产出乱码。"""
    return [
        NormalizedDocument(
            doc_id=source.doc_id,
            source_format="image",
            ir_format="markdown",
            content=content,
            source_path=source.source_path,
            metadata={
                **source.metadata,
                "parser_version": "image/ocr",
                "quality_score": score,
                "ocr_note": reason[:200],
            },
        )
    ]


class ImageOcrNormalizer(Normalizer):
    """图片 → Markdown（RapidOCR）。"""

    source_format = "image"

    def __init__(self, *, settings: Any = None, engine: Any = None) -> None:
        self._settings = settings
        self._engine = engine          # 测试注入；None → 用进程单例

    def normalize(self, source: NormalizedDocument) -> List[NormalizedDocument]:
        path = source.source_path
        enabled = bool(getattr(self._settings, "image_ocr_enabled", True))
        if not enabled:
            return _degraded(source, "", "image_ocr_enabled=false", 0.0)

        if not path:
            return _degraded(source, "", "missing_source_path", 0.4)

        engine = self._engine
        error: Optional[Exception] = None
        if engine is None:
            engine, error = _get_engine()
        if engine is None:
            return _degraded(source, "", f"rapidocr 不可用: {error}", 0.4)

        try:
            result, _elapse = engine(str(path))
        except Exception as exc:  # noqa: BLE001  识别失败 → 降级
            logger.warning("图片 OCR 识别失败 %s: %s", path, exc)
            return _degraded(source, "", f"ocr_error: {type(exc).__name__}", 0.4)

        content = _rows_to_markdown(result or [])
        if not content.strip():
            return _degraded(source, "", "no_text_found（可能是非文字图片）", 0.5)

        return [
            NormalizedDocument(
                doc_id=source.doc_id,
                source_format="image",
                ir_format="markdown",
                content=content,
                source_path=source.source_path,
                metadata={
                    **source.metadata,
                    "parser_version": "rapidocr/v1",
                    "quality_score": 1.0,
                },
            )
        ]


__all__ = ["ImageOcrNormalizer"]
