"""evals.runners — 评测跑批（真实 LLM / Milvus 调用在此层）。"""

from __future__ import annotations

from .router import run_router

__all__ = ["run_router"]
