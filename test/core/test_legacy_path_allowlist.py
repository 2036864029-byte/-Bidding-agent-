"""旧路径引用白名单守卫 — 已迁移壳模块只允许既有消费者引用（方案 P0 补充）。

背景：P0 把 config / llm / embeddings / mysql 池上提到 core/，旧文件只留
re-export 壳（public_kb.config / public_kb.services.llm / public_kb.services.embeddings
/ agent.nodes.price_inquiry.db）。这些壳随各自消费者迁移后逐 Phase 删除
（见方案附录 A "P0 执行状态"）。本守卫防止"后续 Phase 新增代码继续引用旧路径"：
- 白名单 = 迁移前就存在的既有消费者（下表逐一枚举）；
- 任何**不在白名单**的文件引用壳模块 → 测试失败，提示改用 core 路径。

守卫同时捕获绝对 import 与相对 import（from ..config / from .db 等按文件所在
包解析到绝对模块名）。

被门禁的壳模块与迁移目标：
    public_kb.config              → core.config.settings
    public_kb.services.llm        → core.llm.registry / core.llm.deepseek
    public_kb.services.embeddings → core.embeddings.bge_m3
    agent.nodes.price_inquiry.db  → core.rdb.mysql_pool
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 被门禁的壳模块（值 = 建议的 core 迁移目标，仅提示用）
GATED_LEGACY: dict[str, str] = {
    "public_kb.config": "core.config.settings",
    "public_kb.services.llm": "core.llm.registry / core.llm.deepseek",
    "public_kb.services.embeddings": "core.embeddings.bge_m3",
    "agent.nodes.price_inquiry.db": "core.rdb.mysql_pool",
}

# 扫描目录（生产 + 一次性脚本 + 测试；shell 本身在 public_kb/ 下，不 import 自己）
_SCAN_ROOTS = ("core", "agent", "public_kb", "scripts", "test")

# ─────────────────────────────────────────────────────────────
# 白名单：壳模块 → 允许引用的既有消费者（绝对模块名）
# 由首跑枚举迁移前真实消费者固化而来；新增文件/新引用不在此列即失败。
# 说明：
#   - test.core.test_import_compat 是"兼容性专项测试"，专门验证旧路径仍可 import，
#     是白名单的刻意成员；其余为尚未迁移的 public_kb 内部 / price_inquiry 包内 /
#     scripts / 老测试。
#   - 壳随各自消费者迁移后逐 Phase 删除（见方案附录 A），届时同步更新本表。
# ─────────────────────────────────────────────────────────────
ALLOWED_CONSUMERS: dict[str, set[str]] = {
    "public_kb.config": {
        # public_kb 内部（尚未迁移到 core.config，随各模块触达迁移）
        "public_kb",
        "public_kb.__main__",
        "public_kb.generation.chain",
        "public_kb.generation.citations_validate",
        "public_kb.ingestion.cli",
        "public_kb.ingestion.handoff",
        "public_kb.ingestion.parser_factory",
        "public_kb.ingestion.transforms.pdf_mineru_router",
        "public_kb.ingestion.transforms.pdf_router",
        "public_kb.qa_chain",
        "public_kb.rag_engine",
        "public_kb.retrieval.fallback",
        "public_kb.retrieval.milvus_search",
        "public_kb.retrieval.retriever",
        "public_kb.retrieval.strategies",
        "public_kb.services.milvus_store",
        "public_kb.services.mineru_api_parser",
        "public_kb.services.mineru_parser",
        # scripts 一次性工具
        "scripts.pdf_l6_single_book_verify",
        "scripts.poc_ingest_sample",
        "scripts.poc_validate_csv_ingestion",
        "scripts.poc_verify_hybrid",
        "scripts.run_knowledge_citation_eval",
        # 测试（读取 Settings 的老用例；兼容专项）
        "test.core.test_import_compat",
        "test.test_citation_tracing",
        "test.test_dedup_ingestion",
        "test.test_effective_date_filter",
        "test.test_handoff",
        "test.test_milvus_store_offline",
        "test.test_mineru_api_parser",
        "test.test_parser_factory",
        "test.test_pdf_router",
        "test.test_public_kb_layout",
        "test.test_public_kb_offline_gate",
        "test.test_qa_chain_offline",
        "test.test_recall_optimization",
        "test.test_retrieval_strategies",
    },
    "public_kb.services.llm": {
        "public_kb.rag_engine",
        "scripts.poc_verify_hybrid",
        "test.core.test_import_compat",
        "test.test_public_kb_layout",
    },
    "public_kb.services.embeddings": {
        "public_kb.ingestion.cli",
        "public_kb.ingestion.handoff",
        "public_kb.rag_engine",
        "scripts.poc_ingest_sample",
        "scripts.poc_validate_csv_ingestion",
        "scripts.poc_verify_hybrid",
        "test.core.test_import_compat",
        "test.test_public_kb_layout",
    },
    "agent.nodes.price_inquiry.db": {
        # price_inquiry 包内既有消费者（P3 随引擎整体迁 price_engine 后删壳）
        "agent.nodes.price_inquiry",
        "agent.nodes.price_inquiry.enum_norm",
        "agent.nodes.price_inquiry.intent",
        "agent.nodes.price_inquiry.node",
        "agent.nodes.price_inquiry.queries",
        "agent.nodes.price_inquiry.recall",
        "agent.nodes.price_inquiry.semantic",
        "agent.nodes.price_inquiry.sql_builders",
        "test.core.test_import_compat",
    },
}


# ── 相对 import 解析 ──────────────────────────────────────────
def _module_of(path: Path) -> str:
    rel = path.relative_to(PROJECT_ROOT)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _resolve_import(module_path: str, node: ast.AST, is_package: bool) -> str | None:
    """把一条 import 语句解析为被引用模块的绝对名。

    Returns None 表示非相对/无需门禁（如绝对 import 公共库）。
    """
    if isinstance(node, ast.Import):
        return node.names[0].name  # import a.b[.c]
    if isinstance(node, ast.ImportFrom):
        # 绝对 from a.b import x
        if node.level == 0 and node.module:
            return node.module
        # 相对 from . / .. import — 需解析
        if node.level > 0:
            parts = module_path.split(".")
            # 当前模块的"包"：__init__.py 时模块即包；否则去掉末段
            pkg = parts if is_package else parts[:-1]
            # 上溯 (level - 1) 层包
            for _ in range(node.level - 1):
                if pkg:
                    pkg.pop()
            tail = (node.module or "").split(".")
            return ".".join(pkg + tail) if pkg else None
    return None


# ── 收集某文件引用的全部壳模块 ────────────────────────────────
def _referenced_legacy(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        # 本解释器下无法解析的文件（如 scripts 里 py3.12 专属/编码异常的旧脚本）
        # 运行时不可导入 → 无旧路径引用风险，跳过。
        return set()
    module_path = _module_of(path)
    is_package = path.name == "__init__.py"
    refs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        target = _resolve_import(module_path, node, is_package)
        if target:
            for gated in GATED_LEGACY:
                if target == gated or target.startswith(gated + "."):
                    refs.add(gated)
    return refs


def _iter_py():
    for root in _SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if base.exists():
            yield from base.rglob("*.py")


def test_legacy_shell_consumers_are_allowlisted():
    # 自检：白名单必须非空（防空表误删/导入异常导致形同虚设）
    empty = [k for k, v in ALLOWED_CONSUMERS.items() if not v]
    assert not empty, f"白名单存在空集合，守卫失效: {empty}"

    violations: list[str] = []
    for path in _iter_py():
        for gated in _referenced_legacy(path):
            mod = _module_of(path)
            allowed = ALLOWED_CONSUMERS.get(gated, set())
            if mod in allowed:
                continue
            violations.append(
                f"{mod} 引用 {gated}（不在白名单）。迁移目标: {GATED_LEGACY[gated]}"
            )
    assert not violations, (
        "发现旧路径新引用（壳将随消费者迁移后删除，勿新增引用）:\n" + "\n".join(violations)
    )


def test_gated_legacy_set_is_known():
    """防止白名单定义漂移：白名单键必须与门禁集一致。"""
    assert set(ALLOWED_CONSUMERS) == set(GATED_LEGACY)
