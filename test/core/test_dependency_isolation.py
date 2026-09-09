"""依赖隔离守卫（E0-4）— 评测栈不得污染运行时。

三条硬约束（防回潮）：
  1. 运行时包（core/agent/api/orchestration/price_engine/public_kb）不得 import `eval/`
     —— 离线评测包只被 scripts/ 与 test/ 引用。
  2. `ragas` 只允许出现在 `[dependency-groups] dev`，不得进 `[project] dependencies`
     —— 生产 `uv sync --no-dev` 不携带评测依赖。
  3. `import ragas` 冒烟：ragas 依赖 langchain-community 的 `vertexai` 模块，
     后者在 0.4.x 被移除（E0-1 实测）。升级 langchain-community 前本用例即红灯。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_PACKAGES = ("core", "agent", "api", "orchestration", "price_engine", "public_kb")


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_runtime_packages_do_not_import_eval():
    """运行时包不得 import 离线评测包 eval/（评测代码不进服务）。"""
    violations: list[str] = []
    for pkg in RUNTIME_PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # 语法错误由别的用例负责
                continue
            for module in _imported_modules(tree):
                if module == "eval" or module.startswith("eval."):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)} → {module}")
    assert not violations, "运行时包 import 了评测包：\n" + "\n".join(violations)


def test_ragas_is_dev_only_dependency():
    """ragas 只能在 dev 依赖组，不得进运行时依赖。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    runtime_deps = [d.lower() for d in data.get("project", {}).get("dependencies", [])]
    assert not any(d.startswith("ragas") for d in runtime_deps), (
        "ragas 出现在 [project].dependencies —— 评测依赖不得进运行时"
    )

    dev_deps = [
        d.lower()
        for group in data.get("dependency-groups", {}).values()
        for d in group
    ]
    assert any(d.startswith("ragas") for d in dev_deps), "ragas 未登记在 dev 依赖组"


def test_ragas_import_smoke():
    """ragas 可 import（守护 langchain-community 的 vertexai 兼容性，见 E0-1）。"""
    import pytest

    ragas = pytest.importorskip("ragas", reason="dev 依赖未安装（生产环境跳过）")
    from ragas import evaluate  # noqa: F401
    from ragas.metrics import (  # noqa: F401
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    assert ragas is not None
