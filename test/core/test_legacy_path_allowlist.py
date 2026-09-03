"""旧路径壳移除守卫（方案 P0 补充 → P6 结构收敛完成）。

P0 把 config / llm / embeddings / mysql 池上提到 core/，旧文件留 re-export 壳；
P6 结构收敛已随消费者迁移**全部删除**这些壳（public_kb.config / services.llm /
services.embeddings / agent.nodes.price_inquiry.db / rag_engine / qa_chain /
generation.citations / generation.context / contracts / chunk_ids /
services.milvus_store / answer_templates / output_templates）。
本守卫断言这些壳文件不得回潮；新增代码应直接引用 core / 新路径。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 已删除的旧壳模块（值 = 迁移目标，仅文档提示）
REMOVED_SHELLS: dict[str, str] = {
    "public_kb.config": "core.config.settings",
    "public_kb.services.llm": "core.llm.registry / core.llm.deepseek",
    "public_kb.services.embeddings": "core.embeddings.bge_m3",
    "agent.nodes.price_inquiry.db": "core.rdb.mysql_pool",
    "public_kb.rag_engine": "public_kb.engine",
    "public_kb.qa_chain": "public_kb.generation.chain",
    "public_kb.generation.citations": "public_kb.guardrails",
    "public_kb.generation.context": "public_kb.rendering.context",
    "public_kb.contracts": "core.contracts",
    "public_kb.chunk_ids": "core.contracts.chunk_ids",
    "public_kb.services.milvus_store": "core.vectorstore.milvus_client",
    "agent.nodes.answer_templates": "price_engine.rendering.answer_templates",
    "agent.nodes.output_templates": "price_engine.rendering.output_templates",
}

_SHELL_FILES = [
    "public_kb/config.py",
    "public_kb/services/llm.py",
    "public_kb/services/embeddings.py",
    "public_kb/rag_engine.py",
    "public_kb/qa_chain.py",
    "public_kb/generation/citations.py",
    "public_kb/generation/context.py",
    "public_kb/contracts.py",
    "public_kb/chunk_ids.py",
    "public_kb/services/milvus_store.py",
    "agent/nodes/answer_templates.py",
    "agent/nodes/output_templates.py",
    "agent/nodes/price_inquiry/db.py",
]


def test_removed_shells_do_not_come_back():
    """P6 结构收敛：已删除的旧壳模块文件不得回潮（新增代码应直接用 core/新路径）。"""
    existing = [f for f in _SHELL_FILES if (PROJECT_ROOT / f).exists()]
    assert not existing, f"已删除的兼容壳回潮: {existing}"


def test_removed_shell_mapping_documented():
    """移除清单非空且每条都指向明确的迁移目标（防空表误删导致守卫失效）。"""
    assert REMOVED_SHELLS
    for shell, target in REMOVED_SHELLS.items():
        assert target, f"壳 {shell} 缺少迁移目标"
    assert len(_SHELL_FILES) >= len(REMOVED_SHELLS)
