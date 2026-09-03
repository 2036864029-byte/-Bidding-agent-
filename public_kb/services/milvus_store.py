# 功能：Milvus 存储管理——兼容壳（P6 A-8 已上提 core/vectorstore）。
"""兼容壳：MilvusStoreManager 已上提 ``core.vectorstore.milvus_client``。

新代码请直接 ``from core.vectorstore.milvus_client import MilvusStoreManager``。
``MilvusVectorStore`` 为 langchain_milvus 别名，旧路径测试 patch 用，一并 re-export。
"""
from __future__ import annotations

import warnings

warnings.warn(
    "public_kb.services.milvus_store 已迁移至 core.vectorstore.milvus_client；"
    "请改用 `from core.vectorstore.milvus_client import MilvusStoreManager`，"
    "旧路径将在后续阶段移除。",
    DeprecationWarning,
    stacklevel=2,
)

from langchain_milvus import Milvus as MilvusVectorStore  # noqa: F401,E402
from core.vectorstore.milvus_client import MilvusStoreManager  # noqa: F401,E402

__all__ = ["MilvusStoreManager", "MilvusVectorStore"]
