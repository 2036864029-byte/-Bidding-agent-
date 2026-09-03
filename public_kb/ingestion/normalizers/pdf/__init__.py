"""ingestion.normalizers.pdf — PDF 解析模块收敛包（方案 Phase 5 / E-7）。

原 transforms/pdf_*.py 三档路由/复杂页/Markdown 装配等 9 模块 git mv 至此；
旧路径镜像壳已于 P6 结构收敛移除（调用方全部切换至本包）。
"""

from __future__ import annotations

from .normalizer import PdfNormalizer

__all__ = ["PdfNormalizer"]
