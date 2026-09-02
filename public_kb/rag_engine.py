# 功能：PublicKnowledgeRAG 对外门面（兼容壳）— 已改名 public_kb/engine.py（Phase 1 / B-5）。
"""RAG facade（兼容壳）— 实现已迁 public_kb/engine.py。

保留此文件仅为兼容既有 import 路径：
    from public_kb.rag_engine import PublicKnowledgeRAG
    from public_kb import PublicKnowledgeRAG      # 经 __init__ 懒加载 → engine
新代码请使用：
    from public_kb.engine import PublicKnowledgeRAG
"""

from __future__ import annotations

import warnings

from .engine import PublicKnowledgeRAG  # noqa: F401

warnings.warn(
    "public_kb.rag_engine 已改名为 public_kb.engine，"
    "请改用 `from public_kb.engine import PublicKnowledgeRAG`。"
    "本兼容路径将在后续 Phase 移除。",
    DeprecationWarning,
    stacklevel=2,
)
