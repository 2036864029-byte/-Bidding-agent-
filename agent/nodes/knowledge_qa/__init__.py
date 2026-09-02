"""
knowledge_qa 包 — 知识问答节点拆包后的兼容入口（Phase 1 / B-4）。

对外符号与原单文件模块一致：`from agent.nodes.knowledge_qa import node_knowledge_qa`
及 `from agent.nodes import node_knowledge_qa` 均无需改动。
结构：node.py(开关分发) / legacy.py(旧 rag.query 路径) / chain.py(工具路径编排)
/ prompts.py(生成模板)。
"""

from .node import node_knowledge_qa

__all__ = ["node_knowledge_qa"]
