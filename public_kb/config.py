# 功能：集中加载和管理公共知识库所有配置参数（兼容壳）。
"""
统一配置中心（兼容壳）— 配置已上提 core.config.settings（方案 Phase 0 / A-1）。

保留此文件仅为兼容既有 import 路径：
    from public_kb.config import Settings
新代码请直接使用：
    from core.config.settings import Settings

本模块为 re-export，业务逻辑见 core/config/settings.py。
"""

from __future__ import annotations

import warnings

from core.config.settings import Settings, CitationRuleConfig  # noqa: F401

warnings.warn(
    "public_kb.config 已迁移至 core.config.settings，请改用 "
    "`from core.config.settings import Settings`。本兼容路径将在后续 Phase 移除。",
    DeprecationWarning,
    stacklevel=2,
)
