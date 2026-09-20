"""Supervisor 主图 handoff 端到端离线测试（方案 Phase 6 / Step 2 G-5）。

注入 fake agent 子图（callable，同 (state, config) 接口）与 fake L3 router，
不触碰网络。覆盖：price→handoff→knowledge 组合终态、无 handoff 直通 END、
handoff 清空防循环、agent 内部键不泄漏进 supervisor 终态。

`test_real_compiled_subgraph_through_supervisor` 用**真实编译子图**（验 langgraph
不可 callable 的适配回归），其检索入口由 `_support.fakes.patch_kb_search` 打桩，
故同样离线可跑。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from _support.fakes import patch_kb_search
from orchestration.main_graph import build_supervisor_graph

# ── fake agent 子图 ───────────────────────────────────────────

def _fake_price(state, config=None):
    return {
        "business_result": {
            "branch": "price_inquiry",
            "answer": "中标结果：科大讯飞中标「某项目」1条（金额500万）",
            "data": {"records": [{"project_name": "某项目"}], "tables": ["bid_project"]},
        },
        "messages": [AIMessage(content="中标结果：科大讯飞中标「某项目」1条（金额500万）")],
        "pg_internal_secret": 42,  # 模拟子图内部键，应被隔离
    }


def _fake_knowledge(state, config=None):
    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": "根据《招标投标法》第四十六条，中标通知书对招标人和中标人具有法律效力。",
            "data": {"sources": []},
        },
        "messages": [AIMessage(content="根据《招标投标法》第四十六条，中标通知书对招标人和中标人具有法律效力。")],
        "qa_internal_secret": "x",
    }


def _build(router_intent="price_inquiry"):
    fake_router = lambda state: {"router_intent": router_intent}
    return build_supervisor_graph(
        llm_router=fake_router,
        knowledge_subgraph=_fake_knowledge,
        price_subgraph=_fake_price,
    )


# ── handoff 端到端 ────────────────────────────────────────────

def test_handoff_price_to_knowledge_combined_answer():
    g = _build()
    out = g.invoke({"messages": [HumanMessage(
        content="科大讯飞中标了哪些项目？中标通知书有什么法律效力？")]})

    br = out["business_result"]
    # 组合回答：price 结果 + 政策法规解释
    assert "中标结果" in br["answer"]
    assert "法律效力" in br["answer"]
    assert br["answer"].count("【政策法规解释】") == 1
    assert out["messages"][-1].content == br["answer"]
    # handoff 已消费清空（防循环）
    assert out.get("handoff") is None
    assert out.get("pending_context") is None
    # 上一 agent 结果留在 handoff_context
    assert br["data"]["handoff_context"]["branch"] == "price_inquiry"
    assert out["last_agent"] == "knowledge_qa"


def test_no_handoff_direct_end_keeps_agent_answer():
    g = _build()
    out = g.invoke({"messages": [HumanMessage(content="科大讯飞中标了哪些项目？")]})

    br = out["business_result"]
    assert br["branch"] == "price_inquiry"
    assert br["answer"] == "中标结果：科大讯飞中标「某项目」1条（金额500万）"
    assert out["messages"][-1].content == br["answer"]
    assert out.get("handoff") is None
    assert out.get("pending_context") is None


def test_supervisor_state_isolates_agent_internal_keys():
    """agent 内部键（pg_*/qa_*）不得泄漏进 supervisor 终态。"""
    out = _build().invoke({"messages": [HumanMessage(content="科大讯飞中标了哪些项目？")]})

    leaked = [k for k in out if k.endswith("_internal_secret")]
    assert leaked == []


def test_handoff_terminates_without_infinite_loop():
    """handoff 目标（knowledge）完成后即使问题仍含解释关键词也不二次 handoff。"""
    g = _build()
    out = g.invoke({"messages": [HumanMessage(
        content="科大讯飞中标了哪些项目？中标通知书有什么法律效力？")]})
    # 若死循环会超时/栈溢出；此处断言恰好一次组合（末条为合并回答）
    assert out["messages"][-1].content == out["business_result"]["answer"]
    assert "【政策法规解释】" in out["messages"][-1].content


def test_fallback_route_still_terminates():
    fake_router = lambda state: {"router_intent": "fallback"}
    g = build_supervisor_graph(llm_router=fake_router,
                               knowledge_subgraph=_fake_knowledge,
                               price_subgraph=_fake_price)
    out = g.invoke({"messages": [HumanMessage(content="乱七八糟的一句话")]})
    assert out["business_result"]["branch"] == "fallback"
    assert out.get("handoff") is None


def test_real_compiled_subgraph_through_supervisor(monkeypatch):
    """真实编译子图（langgraph 1.2.11 不可 callable）经适配器在 supervisor 主图运行。

    回归：此前 `_with_fallback` 对 CompiledStateGraph 误判单参 → "object is not callable"。

    注意检索入口用共享助手 `patch_kb_search` 一次补两个 —— 曾经只 patch
    `ksg.kb_search`，而 `kb_light_decompose` 默认 True 时子图走
    `kb_search_expanded`，补丁静默失效、真联网到模型隧道（表现："本机跑隧道就绿"）。
    """
    import agent.nodes.knowledge_qa.subgraph as ksg
    from agent.nodes.knowledge_qa.subgraph import build_knowledge_qa_subgraph
    from public_kb.guardrails.citations_models import Citation
    from public_kb.tools import Chunk, RetrievalResult

    triple = RetrievalResult(
        chunks=[Chunk(content="公开招标是指招标人以招标公告方式邀请不特定的法人或其他组织投标。",
                      chunk_id=1, chunk_uid="u1", doc_name="招标投标法", chapter="第一章",
                      chunk_index=0, score=0.9)],
        citations_meta=[Citation(context_index=1, chunk_id=1, chunk_uid="u1",
                                 doc_name="招标投标法", chapter="第一章", chunk_index=0,
                                 text="公开招标是指招标人以招标公告方式邀请不特定的法人或其他组织投标。",
                                 score=0.9)],
        rendered_context="[来源1] 公开招标是指招标人以招标公告方式邀请不特定的法人或其他组织投标。",
        recall_trace={"mode": "fake"},
    )
    patch_kb_search(monkeypatch, ksg, triple)
    monkeypatch.setattr(ksg, "_get_llm", lambda: RunnableLambda(
        lambda msgs: AIMessage(content="根据【来源1】，公开招标是指招标人以招标公告方式邀请不特定的法人或其他组织投标。")))

    fake_router = lambda state: {"router_intent": "knowledge_qa"}
    real_kb = build_knowledge_qa_subgraph()
    g = build_supervisor_graph(llm_router=fake_router, knowledge_subgraph=real_kb)
    out = g.invoke({"messages": [HumanMessage(content="什么是公开招标？")]})

    br = out["business_result"]
    assert br["branch"] == "knowledge_qa"
    assert "公开招标" in br["answer"]
    assert out["messages"][-1].content == br["answer"]
    assert out.get("handoff") is None
    # 子图内部键不泄漏
    assert not [k for k in out if k.startswith(("qa_", "pg_"))]
