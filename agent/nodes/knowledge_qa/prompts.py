"""知识问答提示词（W1-2：模板收敛到 public_kb.rendering.prompts，本文件只保留重试变体）。

- 首轮模板与 LCEL 链路（`public_kb.generation.chain`）共用同一份，不再各存一份副本。
- 重试模板 = 首轮 + 「修改要求」反馈块（护栏失败后重生成用，仅 agent 侧需要）。
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from public_kb.rendering.prompts import (  # noqa: F401  # re-export 供既有导入路径
    INLINE_CITATION_INSTRUCTION,
    USER_TEMPLATE,
    build_prompt,
)

RETRY_USER_TEMPLATE = USER_TEMPLATE + """

修改要求（上一轮回答未通过引用校验，请修正引用标注后重新回答）：
{feedback}"""


def build_retry_prompt(system_text: str, enable_inline_citations: bool = True) -> ChatPromptTemplate:
    """构建护栏失败重试提示词模板（首轮模板 + 修改要求块）。

    Args:
        system_text: system 提示词正文（来自 Settings.system_prompt）。
        enable_inline_citations: 是否要求 LLM 在回答中内联标注【来源N】。
    """
    system = system_text
    if enable_inline_citations:
        system += "\n\n" + INLINE_CITATION_INSTRUCTION
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user",   RETRY_USER_TEMPLATE),
    ])


__all__ = [
    "INLINE_CITATION_INSTRUCTION",
    "USER_TEMPLATE",
    "RETRY_USER_TEMPLATE",
    "build_prompt",
    "build_retry_prompt",
]
