# 功能：知识库契约——兼容壳（P6 A-8 已上提 core/contracts）。
"""兼容壳：知识库契约已上提 ``core.contracts``，本文件 re-export。

新代码请直接 ``from core.contracts import ...``。
"""
from __future__ import annotations

import warnings

warnings.warn(
    "public_kb.contracts 已迁移至 core.contracts；"
    "请改用 `from core.contracts import ...`，旧路径将在后续阶段移除。",
    DeprecationWarning,
    stacklevel=2,
)

from core.contracts import *  # noqa: F401,F403,E402
from core.contracts import __all__  # noqa: F401,E402
