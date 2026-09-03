# 功能：chunk 身份契约——兼容壳（P6 A-8 已上提 core/contracts）。
"""兼容壳：chunk_uid 契约已上提 ``core.contracts.chunk_ids``，本文件 re-export。

新代码请直接 ``from core.contracts.chunk_ids import compute_chunk_uid``。
"""
from __future__ import annotations

import warnings

warnings.warn(
    "public_kb.chunk_ids 已迁移至 core.contracts.chunk_ids；"
    "请改用 `from core.contracts.chunk_ids import ...`，旧路径将在后续阶段移除。",
    DeprecationWarning,
    stacklevel=2,
)

from core.contracts.chunk_ids import (  # noqa: F401,E402
    compute_chunk_uid,
    compute_text_hash,
    normalize_chunk_text,
)

__all__ = ["normalize_chunk_text", "compute_text_hash", "compute_chunk_uid"]
