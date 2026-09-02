# 功能：统一创建 DeepSeek / OpenAI 兼容 Chat LLM（兼容壳）。
"""
LLM 工厂（兼容壳）— 已上提 core.llm.deepseek（方案 Phase 0 / A-6）。

保留此文件仅为兼容既有 import 路径：
    from public_kb.services.llm import create_llm
新代码请使用：
    from core.llm.registry import create_llm_for_slot   # 按槽位取模型（推荐）
    from core.llm.deepseek import create_llm             # 按 Settings 直接构造

实现已迁移，本文件为 re-export，逻辑见 core/llm/deepseek.py。
"""

from __future__ import annotations

import warnings

from core.llm.deepseek import DeepSeekProvider, create_llm  # noqa: F401

warnings.warn(
    "public_kb.services.llm 已迁移至 core.llm.deepseek / core.llm.registry，"
    "请改用 `from core.llm.registry import create_llm_for_slot` 按槽位取模型。"
    "本兼容路径将在后续 Phase 移除。",
    DeprecationWarning,
    stacklevel=2,
)
