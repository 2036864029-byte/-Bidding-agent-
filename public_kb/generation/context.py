# 功能：把检索到的 Document 格式化为 LLM 上下文和返回 sources（兼容壳）。
"""Context formatting（兼容壳）— 已迁 public_kb/rendering/context.py（Phase 1 / B-2）。

保留此文件仅为兼容既有 import：
    from public_kb.generation.context import format_docs, build_sources
新代码请使用：
    from public_kb.rendering.context import format_docs, build_sources
"""

from __future__ import annotations

import warnings

from ..rendering.context import build_sources, format_docs  # noqa: F401

warnings.warn(
    "public_kb.generation.context 已迁移至 public_kb.rendering.context，"
    "请改用 `from public_kb.rendering.context import format_docs`。"
    "本兼容路径将在后续 Phase 移除。",
    DeprecationWarning,
    stacklevel=2,
)
