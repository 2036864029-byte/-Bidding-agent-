"""price_inquiry 子图（方案 Phase 6 / F-4）守卫出口矩阵离线测试。

不触碰网络/DB：_parse_unified_intent / _build_llm / _normalize_intent_enums / _sql_executor
全部打桩。覆盖子图路由正确性——各守卫出口（product / project_detail_no_number /
bare_entity_no_intent / no_entity_for_route / llm_timeout / sql_timeout / POST_GUARD）
经子图产生的 business_result 与既有语义逐键一致；正常渲染出口走通。
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import agent.nodes.price_inquiry.subgraph as sg
from agent.nodes.price_inquiry.subgraph import (
    _UNIFIED_GUIDANCE_TEXT,
    _build_capability_boundary_answer,
    _build_unified_guidance,
    build_price_subgraph,
)
from price_engine.models import HardFilters, SearchIntent


# ── 夹具 ──────────────────────────────────────────────────────

def _intent(sub_route="company_query", query_type="company_detail", *,
            company_name=None, successful_bidder=None, purchaser=None,
            project_number=None, semantic=(), exact=()):
    return SearchIntent(
        hard_filters=HardFilters(
            company_name=company_name,
            successful_bidder=successful_bidder,
            purchaser=purchaser,
            project_number=project_number,
        ),
        semantic_keywords=list(semantic),
        exact_tokens=list(exact),
        original_question="q",
        sub_route=sub_route,
        query_type=query_type,
    )


def _patch_parse(monkeypatch, intent):
    """打桩意图解析链：_build_llm/_parse_unified_intent/_normalize_intent_enums。"""
    monkeypatch.setattr(sg, "_build_llm", lambda: object())
    monkeypatch.setattr(sg, "_parse_unified_intent", lambda q, llm: intent)
    monkeypatch.setattr(sg, "_normalize_intent_enums", lambda i: i)


class _ReadyFuture:
    """立即返回结果的假 Future（模拟 _sql_executor.submit）。"""

    def __init__(self, value):
        self._value = value

    def result(self, timeout=None):
        return self._value


class _ImmediateTimeoutFuture:
    """立即抛超时的假 Future（SQL 超时出口测试用）。"""

    def result(self, timeout=None):
        raise FutureTimeoutError()


def _run(monkeypatch, question="某公司的工商信息", *, sql_value=None, sql_timeout=False):
    if sql_timeout:
        executor = SimpleNamespace(submit=lambda fn, intent: _ImmediateTimeoutFuture())
    elif sql_value is not None:
        executor = SimpleNamespace(submit=lambda fn, intent: _ReadyFuture(sql_value))
    else:
        executor = SimpleNamespace(
            submit=lambda fn, intent: (_ for _ in ()).throw(AssertionError("不应触发 SQL")))
    monkeypatch.setattr(sg, "_sql_executor", executor)
    sub = build_price_subgraph()
    return sub.invoke({"messages": [HumanMessage(content=question)]}, config=None)


# ── 1. 确定性守卫出口 ─────────────────────────────────────────

def test_capability_boundary_helper_shape():
    """product 能力边界文案（守卫矩阵保留项）：business_result 结构合法。"""
    out = _build_capability_boundary_answer("查产品价格")
    assert out["business_result"]["branch"] == "price_inquiry"
    assert out["business_result"]["query_type"] == "capability_boundary"
    assert "不支持产品价格查询" in out["business_result"]["answer"]
    assert out["messages"][0].content == out["business_result"]["answer"]
    # 注：sub_route=product_query 分支为防御性死代码——真实链路 _safe_parse_intent
    # 会把 product_query 归一为 all（test_sub_route::test_product_query_intercepted_to_all），
    # 本函数仅以保留的辅助形态直测。


def test_exit_project_detail_no_number(monkeypatch):
    _patch_parse(monkeypatch, _intent(sub_route="bidding_query", query_type="project_detail"))
    out = _run(monkeypatch, question="项目的中标情况")
    expect = _build_unified_guidance("项目的中标情况", out["pg_intent"], reason="project_detail_no_number")
    assert out["business_result"] == expect["business_result"]


def test_exit_bare_entity_no_intent(monkeypatch):
    # 有效公司名 + 无查询意图关键词 → 裸实体引导
    _patch_parse(monkeypatch, _intent(company_name="科大讯飞股份有限公司", semantic=["科大讯飞"]))
    out = _run(monkeypatch, question="科大讯飞股份有限公司")
    expect = _build_unified_guidance("科大讯飞股份有限公司", out["pg_intent"], reason="bare_entity_no_intent")
    assert out["business_result"] == expect["business_result"]


def test_exit_no_entity_for_route(monkeypatch):
    # company_query 路由但无有效实体 → 第三层引导
    _patch_parse(monkeypatch, _intent(semantic=["科技"]))
    out = _run(monkeypatch, question="查一下科技公司")
    expect = _build_unified_guidance("查一下科技公司", out["pg_intent"], reason="no_entity_for_route")
    assert out["business_result"] == expect["business_result"]


def test_exit_llm_timeout(monkeypatch):
    # 总超时极小 + 确定性 perf_counter → LLM 阶段超 60% 降级
    _patch_parse(monkeypatch, _intent(company_name="科大讯飞股份有限公司"))
    fake_settings = SimpleNamespace(node_total_timeout=0.000001)
    monkeypatch.setattr(sg, "_get_settings", lambda: fake_settings)
    calls = {"n": 0}

    def fake_pc():
        calls["n"] += 1
        return float(calls["n"])  # 单调递增秒数，llm_elapsed 确定性 > 0

    monkeypatch.setattr("time.perf_counter", fake_pc)
    out = _run(monkeypatch, question="科大讯飞股份有限公司的工商信息")
    br = out["business_result"]
    assert br["data"]["error"] == "node_timeout_partial"
    assert br["answer"] == out["messages"][-1].content


# ── 2. SQL 阶段出口 ───────────────────────────────────────────

def test_exit_sql_timeout(monkeypatch):
    _patch_parse(monkeypatch, _intent(company_name="科大讯飞股份有限公司"))
    fake_settings = SimpleNamespace(node_total_timeout=30)
    monkeypatch.setattr(sg, "_get_settings", lambda: fake_settings)
    out = _run(monkeypatch, question="科大讯飞股份有限公司的工商信息", sql_timeout=True)
    br = out["business_result"]
    assert br["data"]["error"] == "node_timeout_sql"
    assert "超时" in br["answer"]


def test_post_guard_no_match_routes_guidance(monkeypatch):
    # bidding_query 召回结果无一匹配目标公司 → POST_GUARD 引导
    intent = _intent(sub_route="bidding_query", query_type="bidder_query",
                     successful_bidder="科大讯飞股份有限公司")
    _patch_parse(monkeypatch, intent)
    fake_settings = SimpleNamespace(node_total_timeout=30)
    monkeypatch.setattr(sg, "_get_settings", lambda: fake_settings)
    sql_value = {
        "records": [{"successful_bidder": "无关公司A", "purchaser": "采购人B"}],
        "queried_tables": ["bid_project"], "sql_count": 1, "total_sql_time": 0.1,
        "total_found": 1,
    }
    out = _run(monkeypatch, question="科大讯飞股份有限公司中标了哪些项目", sql_value=sql_value)
    assert out["business_result"]["data"]["intent"]["guard_reason"].startswith("post_recall_no_match:")


# ── 3. 正常渲染出口（空结果分支，避免模板字段依赖）─────────────

def test_render_empty_result_branch(monkeypatch):
    intent = _intent(company_name="科大讯飞股份有限公司")
    _patch_parse(monkeypatch, intent)
    fake_settings = SimpleNamespace(node_total_timeout=30)
    monkeypatch.setattr(sg, "_get_settings", lambda: fake_settings)
    sql_value = {
        "records": [], "queried_tables": ["company_info"],
        "sql_count": 1, "total_sql_time": 0.05, "total_found": 0,
    }
    out = _run(monkeypatch, question="科大讯飞股份有限公司的工商信息", sql_value=sql_value)
    br = out["business_result"]
    assert br["branch"] == "price_inquiry"
    assert br["sub_route"] == "company_query"
    assert br["data"]["records"] == []
    assert br["answer"] == out["messages"][-1].content


# ── 4. 父图接线（与 knowledge_qa 同构）────────────────────────

def test_subgraph_as_parent_node_isolates_pg_keys(monkeypatch):
    _patch_parse(monkeypatch, _intent(company_name="科大讯飞股份有限公司"))
    fake_settings = SimpleNamespace(node_total_timeout=30)
    monkeypatch.setattr(sg, "_get_settings", lambda: fake_settings)
    sql_value = {
        "records": [], "queried_tables": ["company_info"],
        "sql_count": 1, "total_sql_time": 0.0, "total_found": 0,
    }
    executor = SimpleNamespace(submit=lambda fn, intent: _ReadyFuture(sql_value))
    monkeypatch.setattr(sg, "_sql_executor", executor)

    from langgraph.graph import END, StateGraph
    from agent.state import AgentState

    sub = build_price_subgraph()
    parent = StateGraph(AgentState)
    parent.add_node("price_inquiry", sub)
    parent.set_entry_point("price_inquiry")
    parent.add_edge("price_inquiry", END)
    out = parent.compile().invoke({"messages": [HumanMessage(content="科大讯飞股份有限公司的工商信息")]})

    leaked = [k for k in out if k.startswith("pg_")]
    assert leaked == []
    assert out["business_result"]["branch"] == "price_inquiry"
    assert out["messages"][-1].content == out["business_result"]["answer"]
