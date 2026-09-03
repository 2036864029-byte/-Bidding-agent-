"""orchestration — 多 Agent 主图（Supervisor 形态，方案 Phase 6 / Step 2）。

Supervisor 主图把 Step 1 的 compiled subgraph（knowledge_qa / price_inquiry）当作
「agent」节点调度：三级路由（规则→语义→LLM）分发到目标 agent；agent 完成后由
`handoff_decider` 决定是否跨 agent 交接（如 price 查询需政策解释 → 转 knowledge）。

分层约定：
  - agent 子图零 handoff 认知（handoff 决策全在 supervisor 层），保证 single 模式
    行为逐字节不变（双跑 eval 不降）。
  - orchestration 只经 public_kb.tools / guardrails / rendering 门面复用 RAG，不直连
    public_kb 内部（布局守卫，见 test/core/test_no_reverse_dep.py）。
"""

from .handoff import HandoffMeta, AGENT_NODES, build_sub_question
from .supervisor import (
    classify_rules,
    classify_semantic,
    decide_handoff,
    route_question,
    build_supervisor_node,
)
from .main_graph import SupervisorState, build_supervisor_graph

__all__ = [
    "HandoffMeta",
    "AGENT_NODES",
    "build_sub_question",
    "classify_rules",
    "classify_semantic",
    "decide_handoff",
    "route_question",
    "build_supervisor_node",
    "SupervisorState",
    "build_supervisor_graph",
]
