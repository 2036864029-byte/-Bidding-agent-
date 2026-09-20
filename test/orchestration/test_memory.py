"""Supervisor 主图记忆验证（方案 Phase 6 / Step 5 收尾）。

验证两条已具备的机制：
  1. checkpointer 按 thread_id 隔离——多轮历史在 thread 内累积、thread 间不串扰；
  2. handoff 上下文经 messages 传递——交接注入的子问题留在本 thread 历史，
     且不泄漏到其他 thread；交接控制字段（handoff/pending_context）轮次后清空。

全部离线：fake agent 子图 + fake L3 router，显式 MemorySaver。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from orchestration.main_graph import build_supervisor_graph

# ── fake agent 子图 ───────────────────────────────────────────

def _fake_price(state, config=None):
    q = str(state["messages"][-1].content)[:10]
    return {
        "business_result": {"branch": "price_inquiry", "answer": f"[price]{q}",
                            "data": {"records": []}},
        "messages": [AIMessage(content=f"[price]{q}")],
    }


def _fake_knowledge(state, config=None):
    q = str(state["messages"][-1].content)[:10]
    return {
        "business_result": {"branch": "knowledge_qa", "answer": f"[kb]{q}",
                            "data": {"sources": []}},
        "messages": [AIMessage(content=f"[kb]{q}")],
    }


def _build():
    fake_router = lambda state: {"router_intent": "price_inquiry"}
    return build_supervisor_graph(
        llm_router=fake_router,
        knowledge_subgraph=_fake_knowledge,
        price_subgraph=_fake_price,
        checkpointer=MemorySaver(),
    )


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ── 1. thread_id 隔离与多轮累积 ─────────────────────────────

def test_thread_isolation_and_history_accumulation():
    g = _build()

    # 线程 t1 第一轮
    g.invoke({"messages": [HumanMessage(content="问题甲")]}, config=_cfg("t1"))
    # 线程 t2 全新：不携带 t1 任何消息
    state2 = g.get_state(_cfg("t2")).values
    assert len(state2.get("messages", [])) == 0

    # 线程 t1 第二轮：历史累积（问题甲 与 问题乙 都在）
    g.invoke({"messages": [HumanMessage(content="问题乙")]}, config=_cfg("t1"))
    contents = [m.content for m in g.get_state(_cfg("t1")).values["messages"]]
    assert "问题甲" in contents
    assert "问题乙" in contents
    # t2 仍不受影响
    assert len(g.get_state(_cfg("t2")).values.get("messages", [])) == 0


# ── 2. handoff 上下文经 messages 传递 + 跨 thread 不串扰 ─────

def test_handoff_context_stays_in_thread():
    g = _build()

    # 线程 t1：handoff 问题（含法规解释关键词）→ price → handoff → knowledge
    out1 = g.invoke(
        {"messages": [HumanMessage(content="中标通知书有什么法律效力？")]},
        config=_cfg("t1"),
    )
    # 交接注入的子问题（原问题）进入 messages 历史
    msgs1 = [m.content for m in out1["messages"]]
    assert "中标通知书有什么法律效力？" in msgs1
    # 组合回答 = price 段 + 政策法规解释段
    assert "【政策法规解释】" in out1["business_result"]["answer"]
    # 控制字段轮次后清空（防下一轮误路由/串扰）
    assert out1.get("handoff") is None
    assert out1.get("pending_context") is None

    # 线程 t2：不同 handoff 问题独立运行，不共享 t1 的 handoff 上下文
    out2 = g.invoke(
        {"messages": [HumanMessage(content="处罚依据是什么？")]},
        config=_cfg("t2"),
    )
    assert "【政策法规解释】" in out2["business_result"]["answer"]
    # 各 thread 历史只含自己的问题（无跨 thread 串扰）
    t1_msgs = [m.content for m in g.get_state(_cfg("t1")).values["messages"]]
    t2_msgs = [m.content for m in g.get_state(_cfg("t2")).values["messages"]]
    assert any("中标通知书" in m for m in t1_msgs)
    assert not any("处罚依据" in m for m in t1_msgs)
    assert any("处罚依据" in m for m in t2_msgs)
    assert not any("中标通知书" in m for m in t2_msgs)


# ── 3. 非 handoff 轮次后，控制字段不残留 ────────────────────

def test_control_fields_cleared_after_plain_turn():
    g = _build()
    # 无解释关键词 → price 直答，无 handoff
    out = g.invoke({"messages": [HumanMessage(content="查中标项目")]}, config=_cfg("t1"))
    assert "handoff" not in out or out.get("handoff") is None
    assert "pending_context" not in out or out.get("pending_context") is None
    assert "【政策法规解释】" not in out["business_result"]["answer"]
