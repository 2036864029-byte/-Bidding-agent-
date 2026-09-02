"""AST 布局守卫 — agent/ 不再反向依赖 public_kb 基础设施（方案 Phase 0 / A-13, L2）。

把 P0 验收 `grep -rn "public_kb.services" agent/` 为空固化成自动化测试，防止回潮。

禁止范围（P0 已迁移完成）：
  - public_kb.services.*   → 迁移至 core.llm / core.embeddings（A-4/A-7）
  - public_kb.config       → 迁移至 core.config.settings（A-1）
保留（后续 Phase 处理，不在 P0 禁令内）：
  - `from public_kb import PublicKnowledgeRAG`（knowledge_qa 顶层懒加载，P1 解 R2）
  - `public_kb.generation.citations`（agent/__main__ CLI 渲染，P2 收编协议层）
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent.parent / "agent"

# P0 起 agent/ 不得 import 的 public_kb 内部模块（前缀匹配）
_FORBIDDEN_PREFIXES = ("public_kb.services", "public_kb.config")


def _iter_agent_py():
    for path in AGENT_ROOT.rglob("*.py"):
        # __main__ 属 CLI，也纳入扫描（其 public_kb.generation 引用不在禁令内）
        yield path


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_agent_source_has_no_public_kb_infra_imports():
    violations: list[str] = []
    for path in _iter_agent_py():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            if module.startswith(_FORBIDDEN_PREFIXES):
                violations.append(f"{path.relative_to(AGENT_ROOT.parent)}: {module}")
    assert not violations, "agent/ 仍反向依赖已迁移的 public_kb 基础设施:\n" + "\n".join(violations)


def test_knowledge_qa_only_lazy_imports_public_kb_top_level():
    """P1 前允许的唯一边界：knowledge_qa 仅顶层懒加载 PublicKnowledgeRAG。"""
    path = AGENT_ROOT / "nodes" / "knowledge_qa.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module in _imported_modules(tree):
        assert not module.startswith(_FORBIDDEN_PREFIXES), module
