"""echo_demo — P4 演示能力节点（验证「登记即接入」：router/graph 零修改）。

本能力默认在 registry 中 enabled=False（预留防误路由）。演示接入 = 两步：
1. registry 登记一个 enabled 的 CapabilitySpec（见 agent/registry._seed_echo_demo）；
2. 本存储登记 entry（tests/装配层调用 agent.nodes.base.register("echo_demo", node_echo_demo)）。
之后 build_graph/router 自动纳入该能力，无需改 router.py / graph.py。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from ..state import AgentState


def node_echo_demo(state: AgentState) -> dict:
    """回显最后一条用户消息（演示能力）。"""
    messages = state.get("messages") or []
    text = str(messages[-1].content) if messages else ""
    answer = f"回显：{text}"
    return {
        "business_result": {
            "branch": "echo_demo",
            "answer": answer,
            "data": {"echo": text},
        },
        "messages": [AIMessage(content=answer)],
    }
