# 功能：RAG 问答提示词模板（渲染层单一来源）。
"""Prompt construction — 由 public_kb/generation/prompts.py 迁入（W1-2）。

W1-2：该模板原先在 `public_kb/generation/prompts.py` 与
`agent/nodes/knowledge_qa/prompts.py` 各存一份、逐字重复。现收敛到渲染层
（`public_kb.rendering` 是 agent 侧允许的复用门面），两条链路共用同一份：
  - `public_kb/generation/chain.py`（LCEL 链路 / PublicKnowledgeRAG）
  - `agent/nodes/knowledge_qa/subgraph.py`（工具链路，经 agent 侧 prompts.py 转发）
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

INLINE_CITATION_INSTRUCTION = (
    "回答时必须在相关结论句末标注引用来源编号，格式如【来源1】【来源2】。\n"
    "未使用的参考资料不要标注其编号。"
)

USER_TEMPLATE = """参考资料：
{context}

用户问题：{question}"""


def build_prompt(system_text: str, enable_inline_citations: bool = True) -> ChatPromptTemplate:
    """构建问答提示词模板。

    Args:
        system_text: system 提示词正文（来自 Settings.system_prompt）。
        enable_inline_citations: 是否要求 LLM 在回答中内联标注【来源N】。
    """
    system = system_text
    if enable_inline_citations:
        system += "\n\n" + INLINE_CITATION_INSTRUCTION
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user",   USER_TEMPLATE),
    ])


__all__ = ["INLINE_CITATION_INSTRUCTION", "USER_TEMPLATE", "build_prompt"]
