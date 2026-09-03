"""orchestration.handoff — 跨 agent 交接协议（方案 Phase 6 / Step 2 G-1）。

`HandoffMeta{from, to, reason, payload}`：supervisor 层在 agent 完成后的交接决定。
上下文经 messages 传递（P6 步骤 5 约定）——handoff_decider 把 payload 中的子问题
注入为新 HumanMessage 再路由目标 agent；`pending_context` 保存上一 agent 结果供终态组合。

agent 子图本身对 handoff 零认知（决策全在 supervisor 层）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HandoffMeta(BaseModel):
    """一次跨 agent 交接的声明（supervisor 层产生，供条件边路由）。"""

    from_agent: str = Field(description="交接发起方 agent id")
    to: str = Field(description="目标 agent id（必须是 AGENT_NODES 之一）")
    reason: str = Field(default="", description="交接原因（人可读，日志/可观测）")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="交接上下文（sub_question / context_answer 等）"
    )

    def __str__(self) -> str:  # 兼容"经 messages 传递"的可读性
        return f"handoff({self.from_agent}→{self.to}: {self.reason})"


# supervisor 主图可调度的 agent 节点集合（registry 中 enabled 能力的子集）
AGENT_NODES: Dict[str, str] = {
    "knowledge_qa": "knowledge_qa",
    "price_inquiry": "price_inquiry",
    "general_chat": "general_chat",
    "doc_qa": "doc_qa",
    "fallback": "fallback",
}


def build_sub_question(question: str) -> str:
    """从原始问题派生交接子问题（Step 2 简化：直接取原问题）。

    原问题经 kb_search 检索会命中目标法规块（如「中标通知书法律效力」），生成侧
    会聚焦可答部分；后续 Step 如需更精确的子问题拆分（提取「解释/为什么」之后
    的片段）再在此扩展。
    """
    return question
