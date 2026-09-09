"""evals — 离线评测包（E1~E4）。

**命名说明**：目录名用 `evals` 而非计划初稿的 `eval`——`eval` 是 Python 内建函数名，
作为可 import 的包名会遮蔽内建（`import eval` 后该模块内 `eval()` 失效）。语义等价。

**硬约束**：运行时包（core / agent / api / orchestration / price_engine / public_kb）
不得 import 本包，由 `test/core/test_dependency_isolation.py` 守卫。评测依赖（ragas）
只登记在 dev 依赖组，生产 `uv sync --no-dev` 不携带。
"""

from __future__ import annotations

__all__: list[str] = []
