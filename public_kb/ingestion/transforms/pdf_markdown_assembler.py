"""public_kb.ingestion.transforms.pdf_markdown_assembler — 兼容壳（P5 E-7：已收敛 normalizers/pdf/）。

镜像 normalizers/pdf/pdf_markdown_assembler 全部符号（含私有名），保旧路径
`from public_kb.ingestion.transforms.pdf_markdown_assembler import ...` 可用。本壳在 P5 验收后移除。
"""

from __future__ import annotations

import warnings

from ..normalizers.pdf import pdf_markdown_assembler as _m

globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
__all__ = [k for k in vars(_m) if not k.startswith("__")]

warnings.warn(
    "public_kb.ingestion.transforms.pdf_markdown_assembler 已收敛至 normalizers/pdf/pdf_markdown_assembler，"
    "本兼容壳将在 P5 验收后移除。",
    DeprecationWarning,
    stacklevel=2,
)
