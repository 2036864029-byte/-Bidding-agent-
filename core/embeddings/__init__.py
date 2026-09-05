# 功能：core/embeddings 包。
"""core.embeddings — Embedding 模型迁移（方案 Phase 0 / A-7）。

进程级 Embedding 单例（9.3 Track A）：``get_embeddings()`` 全工程共享一个
OpenAIEmbeddings 实例（API 型 embedding 的成本与一致性）；``create_embeddings``
工厂签名不变（兼容既有调用面）。测试经 monkeypatch ``bge_m3.create_embeddings``
注入假实例。
"""

from __future__ import annotations

import threading
from typing import Optional

from langchain_openai import OpenAIEmbeddings

from ..config.settings import Settings
from . import bge_m3
from .bge_m3 import create_embeddings  # noqa: F401  # re-export 保持既有导入面

__all__ = ["create_embeddings", "get_embeddings", "reset_embeddings"]

_lock = threading.Lock()
_embeddings: Optional[OpenAIEmbeddings] = None


def get_embeddings(settings: Optional[Settings] = None) -> OpenAIEmbeddings:
    """进程级 Embedding 单例（带锁惰性，线程安全）。

    Args:
        settings: 全局配置；None 时新建 Settings()（与 create_llm_for_slot 一致）。
    """
    global _embeddings
    if _embeddings is None:
        with _lock:
            if _embeddings is None:
                _embeddings = bge_m3.create_embeddings(settings or Settings())
    return _embeddings


def reset_embeddings() -> None:
    """清空单例（测试/重载用）。"""
    global _embeddings
    _embeddings = None
