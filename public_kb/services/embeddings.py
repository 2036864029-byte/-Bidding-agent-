# 功能：创建和管理 BGE-M3 等 embedding 向量化服务（兼容壳）。
"""
向量化服务（兼容壳）— 已上提 core.embeddings.bge_m3（方案 Phase 0 / A-7）。

保留此文件仅为兼容既有 import 路径：
    from public_kb.services.embeddings import create_embeddings
新代码请使用：
    from core.embeddings.bge_m3 import create_embeddings
"""

from __future__ import annotations

import warnings

from core.embeddings.bge_m3 import (  # noqa: F401
    EMBEDDING_MODEL_ID,
    EMBEDDING_VERSION,
    _SafeEmbeddings,
    create_embeddings,
)

warnings.warn(
    "public_kb.services.embeddings 已迁移至 core.embeddings.bge_m3，"
    "请改用 `from core.embeddings.bge_m3 import create_embeddings`。"
    "本兼容路径将在后续 Phase 移除。",
    DeprecationWarning,
    stacklevel=2,
)
