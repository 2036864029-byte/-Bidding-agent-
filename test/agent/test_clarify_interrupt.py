"""澄清挂起 / 中断续跑测试（HITL）。

覆盖：
  1. `HITL_CLARIFY=false`（默认）：clarify 直接终态，不挂起（兼容既有前端）；
  2. `HITL_CLARIFY=true`：图在 clarify 挂起 → 返回 __interrupt__（含候选）；
  3. `Command(resume=答复)` 从断点续跑：答复变成新的用户消息，断点标记 resolved；
  4. 续跑答复为空（用户放弃）→ 终态文案，不再挂起；
  5. 公开契约：`_to_public_result` 把中断摊平成 interrupted/interrupt。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from agent.graph import AgentGraph
from agent.nodes.clarify import CLARIFY_ANSWER, CLARIFY_EMPTY_ANSWER, node_clarify
from agent.state import AgentState


def _build_mini_graph():
    """最小图：clarify 单节点 + MemorySaver（隔离测节点与断点机制，不牵扯路由/LLM）。"""
    g = StateGraph(AgentState)
    g.add_node("clarify", node_clarify)
    g.set_entry_point("clarify")
    g.add_edge("clarify", END)
    return g.compile(checkpointer=MemorySaver())


def _state(round_no: int = 0) -> dict:
    return {
        "messages": [HumanMessage(content="那个怎么样？")],
        "business_result": {
            "branch": "clarify",
            "data": {"candidates": ["查价格", "查法规"], "original": "那个怎么样？",
                     "round": round_no},
        },
    }


@pytest.fixture()
def hitl_on(monkeypatch):
    monkeypatch.setenv("HITL_CLARIFY", "true")


# ── 同步终态（默认）──────────────────────────────────────────


def test_sync_mode_does_not_interrupt(monkeypatch):
    monkeypatch.setenv("HITL_CLARIFY", "false")
    graph = _build_mini_graph()
    out = graph.invoke(_state(), {"configurable": {"thread_id": "t-sync"}})

    assert "__interrupt__" not in out
    assert out["business_result"]["answer"] == CLARIFY_ANSWER
    assert out["business_result"]["data"]["candidates"] == ["查价格", "查法规"]
    assert out["business_result"]["data"]["resolved"] is False


# ── 挂起 ─────────────────────────────────────────────────────


def test_hitl_suspends_with_candidates(hitl_on):
    graph = _build_mini_graph()
    out = graph.invoke(_state(), {"configurable": {"thread_id": "t-hitl"}})

    interrupts = out.get("__interrupt__")
    assert interrupts, "开启 HITL 后应停在 interrupt 上"
    payload = interrupts[0].value
    assert payload["type"] == "clarification"
    assert payload["question"] == CLARIFY_ANSWER
    assert payload["candidates"] == ["查价格", "查法规"]
    assert payload["original"] == "那个怎么样？"


# ── 续跑 ─────────────────────────────────────────────────────


def test_resume_carries_answer_back(hitl_on):
    graph = _build_mini_graph()
    cfg = {"configurable": {"thread_id": "t-resume"}}
    graph.invoke(_state(), cfg)

    out = graph.invoke(Command(resume="查法规"), cfg)

    assert "__interrupt__" not in out
    assert out["messages"][-1].content == "查法规"
    assert out["messages"][-1].type == "human"  # 答复作为**新的用户消息**回主图
    assert out["business_result"]["data"]["resolved"] is True
    assert out["business_result"]["data"]["round"] == 1


def test_resume_with_candidate_dict(hitl_on):
    """前端点候选 chips：载荷可以是 {"candidate": ...}。"""
    graph = _build_mini_graph()
    cfg = {"configurable": {"thread_id": "t-candidate"}}
    graph.invoke(_state(), cfg)

    out = graph.invoke(Command(resume={"candidate": "查价格"}), cfg)
    assert out["messages"][-1].content == "查价格"


def test_resume_empty_answer_ends_gracefully(hitl_on):
    """用户放弃（空答复）→ 终态文案，不再挂起。"""
    graph = _build_mini_graph()
    cfg = {"configurable": {"thread_id": "t-cancel"}}
    graph.invoke(_state(), cfg)

    out = graph.invoke(Command(resume=""), cfg)
    assert "__interrupt__" not in out
    assert out["business_result"]["answer"] == CLARIFY_EMPTY_ANSWER
    assert out["business_result"]["data"]["cancelled"] is True


def test_no_second_interrupt_after_resume(hitl_on):
    """续跑后 round=1，不再重复挂起（避免连环追问）。"""
    graph = _build_mini_graph()
    cfg = {"configurable": {"thread_id": "t-round"}}
    graph.invoke(_state(), cfg)
    first = graph.invoke(Command(resume="查法规"), cfg)
    assert "__interrupt__" not in first

    # 再喂一轮（round 已被置为 1）也不该挂起
    again = graph.invoke(_state(round_no=1), cfg)
    assert "__interrupt__" not in again


# ── 公开契约 ─────────────────────────────────────────────────


def test_public_result_surfaces_interrupt():
    fake = SimpleNamespace(id="int-1",
                           value={"type": "clarification", "question": "选哪个？",
                                  "candidates": ["a", "b"]})
    out = AgentGraph._to_public_result({
        "messages": [], "business_result": {}, "__interrupt__": [fake],
    })
    assert out["interrupted"] is True
    assert out["interrupt"]["id"] == "int-1"
    assert out["interrupt"]["candidates"] == ["a", "b"]


def test_public_result_without_interrupt():
    out = AgentGraph._to_public_result({
        "messages": [], "business_result": {"answer": "ok"}, "router_intent": "general_chat",
    })
    assert out["interrupted"] is False
    assert out["interrupt"] is None
    assert out["intent"] == "general_chat"
