"""W4-4 水平扩展：状态外置验证。

多副本的前提是**状态不在进程内存里**。用同一 SQLite 文件建两个独立图实例
（模拟两个进程/副本），验证会话历史跨实例可见；并给出 MemorySaver 的对照
（进程内存 → 副本间不共享，多副本会丢会话）。

离线，不触网。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.checkpointer import create_checkpointer
from orchestration.main_graph import build_supervisor_graph


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


def _build(checkpointer):
    return build_supervisor_graph(
        llm_router=lambda state: {"router_intent": "price_inquiry"},
        knowledge_subgraph=_fake_knowledge,
        price_subgraph=_fake_price,
        checkpointer=checkpointer,
    )


_CFG = {"configurable": {"thread_id": "t1"}}


def _contents(graph) -> list[str]:
    return [m.content for m in graph.get_state(_CFG).values.get("messages", [])]


def test_sqlite_checkpointer_shares_state_across_instances(tmp_path):
    """SQLite 外置 → 实例 B（另一进程）能读到实例 A 写的历史，并可继续多轮。"""
    db = str(tmp_path / "ck.db")

    g1 = _build(create_checkpointer("sqlite", connection_string=db))
    g1.invoke({"messages": [HumanMessage(content="问题甲")]}, config=_CFG)

    # 第二个实例：同一 DB，全新进程内状态 → 仍能看到实例 A 的历史
    g2 = _build(create_checkpointer("sqlite", connection_string=db))
    assert "问题甲" in _contents(g2)

    # 实例 B 继续同一线程 → 历史跨实例累积
    g2.invoke({"messages": [HumanMessage(content="问题乙")]}, config=_CFG)
    assert {"问题甲", "问题乙"} <= set(_contents(g2))

    # 实例 A 重新读取 → 也能看到实例 B 写的（双向共享）
    assert "问题乙" in _contents(g1)


def test_memory_checkpointer_is_not_shared_across_instances():
    """对照：默认 MemorySaver 在进程内存 → 第二个实例读不到（多副本会丢会话）。"""
    g1 = _build(MemorySaver())
    g1.invoke({"messages": [HumanMessage(content="问题甲")]}, config=_CFG)
    assert "问题甲" in _contents(g1)

    g2 = _build(MemorySaver())
    assert _contents(g2) == []   # 全新实例，状态不共享


def test_sweep_expired_threads_from_checkpointer(tmp_path):
    """W4-4：从检查点源头枚举+清理线程（多副本安全，不依赖进程内登记表）。"""
    import time as _t

    from agent.checkpointer import list_threads, sweep_expired_threads

    cp = create_checkpointer("sqlite", connection_string=str(tmp_path / "ck.db"))
    g = _build(cp)
    g.invoke({"messages": [HumanMessage(content="甲")]},
             config={"configurable": {"thread_id": "t1"}})
    g.invoke({"messages": [HumanMessage(content="乙")]},
             config={"configurable": {"thread_id": "t2"}})

    assert {"t1", "t2"} <= set(list_threads(cp))

    # TTL 极大 → 都不删
    assert sweep_expired_threads(cp, 999999) == []
    assert {"t1", "t2"} <= set(list_threads(cp))

    # 时间推到 TTL 之后 → 全部过期并删除
    expired = sweep_expired_threads(cp, 60, now=_t.time() + 3600)
    assert set(expired) >= {"t1", "t2"}
    assert list_threads(cp) == {}
