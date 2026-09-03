"""agent.nodes.output_templates — 兼容壳（P3 D-4：模板已下沉 price_engine/rendering）。

镜像 price_engine/rendering/output_templates 全部符号（含私有名），保旧路径可用。
本壳在 P3 验收后随旧路径移除。
"""

from __future__ import annotations

import warnings

from price_engine.rendering import output_templates as _m

globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
__all__ = [k for k in vars(_m) if not k.startswith("__")]

warnings.warn(
    "agent.nodes.output_templates 已迁移至 price_engine.rendering.output_templates，"
    "本兼容壳将在 P3 验收后移除。",
    DeprecationWarning,
    stacklevel=2,
)
