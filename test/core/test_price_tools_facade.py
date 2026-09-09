"""price_engine tools 门面离线测试（方案 Phase 3 / D-8）。

mock 意图解析/归一化与查询函数，不触 LLM/DB。验证 sub_route 路由分发、
product 能力边界、tool 包装形态。
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from price_engine import tools as tools_mod
from price_engine.models import HardFilters, SearchIntent
from price_engine.tools import price_query, price_query_as_tool


def _intent(sub_route: str = "company_query") -> SearchIntent:
    """构造带有效实体的意图（W1-3 后 tools 入口会跑共享守卫，无实体会被拦截）。"""
    if sub_route == "bidding_query":
        filters = HardFilters(successful_bidder="某某科技有限公司")
    else:
        filters = HardFilters(company_name="某某科技有限公司")
    return SearchIntent(
        hard_filters=filters, semantic_keywords=["x"], sub_route=sub_route,
    )


def _patch_intent_flow(monkeypatch, sub_route: str):
    monkeypatch.setattr(tools_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(tools_mod, "_parse_unified_intent",
                        lambda q, llm: _intent(sub_route))
    monkeypatch.setattr(tools_mod, "_safe_parse_intent", lambda i: i)
    monkeypatch.setattr(tools_mod, "_normalize_intent_enums", lambda i: i)


def test_price_query_routes_company(monkeypatch):
    _patch_intent_flow(monkeypatch, "company_query")
    called: dict = {}

    def fake_company(intent):  # noqa: ARG001
        called["fn"] = "company"
        return {"records": [{"company_name": "A"}], "total_found": 1}

    # 分发表是模块级常量（import 时已绑定原函数），须 setitem 打字典
    monkeypatch.setitem(tools_mod._SUB_ROUTE_QUERY_FN, "company_query", fake_company)
    r = price_query("查一下某公司的工商信息")
    assert called.get("fn") == "company"
    assert r["records"][0]["company_name"] == "A"


def test_price_query_routes_bidding(monkeypatch):
    _patch_intent_flow(monkeypatch, "bidding_query")
    called: dict = {}

    def fake_bidding(intent):  # noqa: ARG001
        called["fn"] = "bidding"
        return {"records": [], "total_found": 0}

    monkeypatch.setitem(tools_mod._SUB_ROUTE_QUERY_FN, "bidding_query", fake_bidding)
    price_query("某公司中标了哪些项目")
    assert called.get("fn") == "bidding"


def test_price_query_falls_back_to_all(monkeypatch):
    _patch_intent_flow(monkeypatch, "unknown_route")
    called: dict = {}

    def fake_all(intent):  # noqa: ARG001
        called["fn"] = "all"
        return {"records": [], "total_found": 0}

    # 兜底是 .get(sub_route, _query_all_tables) 的默认参数，call-time 解析模块全局
    monkeypatch.setattr(tools_mod, "_query_all_tables", fake_all)
    price_query("随便查查")
    assert called.get("fn") == "all"


def test_price_query_product_boundary(monkeypatch):
    _patch_intent_flow(monkeypatch, "product_query")
    r = price_query("查一下产品价格")
    assert r["records"] == []
    assert r["notice"] == "product 功能线已下线"


def test_price_query_as_tool_shape():
    tool = price_query_as_tool()
    assert tool.name == "price_query"
    assert "question" in tool.args
    assert "结构化业务数据查询" in tool.description


# ── W1-3：tools 入口与子图共用守卫 ────────────────────────────

def test_price_query_refuses_without_valid_entity(monkeypatch):
    """无有效实体时显式拒绝（与子图引导一致），不再无脑查全表。"""
    monkeypatch.setattr(tools_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(
        tools_mod, "_parse_unified_intent",
        lambda q, llm: SearchIntent(hard_filters=HardFilters(), sub_route="company_query"),
    )
    monkeypatch.setattr(tools_mod, "_safe_parse_intent", lambda i: i)
    monkeypatch.setattr(tools_mod, "_normalize_intent_enums", lambda i: i)

    called = {"fn": False}
    monkeypatch.setitem(
        tools_mod._SUB_ROUTE_QUERY_FN, "company_query",
        lambda intent: called.__setitem__("fn", True) or {"records": []},
    )

    result = price_query("随便问点啥")

    assert result["records"] == []
    assert result["guard_reason"] == "no_entity_for_route"
    assert called["fn"] is False, "守卫拦截后不得执行查询"


def test_guards_are_shared_with_subgraph():
    """W1-3：子图与 tools 入口引用同一守卫实现（同一函数对象，杜绝双份维护）。"""
    import agent.nodes.price_inquiry.subgraph as sg
    from price_engine import guards

    assert sg._has_valid_query_entity is guards.has_valid_query_entity
    assert sg._QUERY_INTENT_KEYWORDS is guards.QUERY_INTENT_KEYWORDS
