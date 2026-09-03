"""ingestion.normalizers.pdf — PDF 解析模块收敛包（方案 Phase 5 / E-7）。

原 transforms/pdf_*.py 三档路由/复杂页/Markdown 装配等 9 模块 git mv 至此；
旧路径 transforms/pdf_*.py 保留镜像壳（L2 import 兼容）。
"""

from __future__ import annotations

from .normalizer import PdfNormalizer

__all__ = ["PdfNormalizer"]
