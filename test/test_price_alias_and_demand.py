"""price_engine 公司简称字号族 + 采购需求 修复测试（2026-09-16，零网络）。

覆盖两个已知问题：
  A. 「杭州海康威视最近中标了哪些项目？」—— 简称被 _is_valid_company_name 拒 → 引导。
     修复：alias.resolve_company_families 字号族解析（杭州海康威视→海康威视），
     守卫放行 + SQL 按 LIKE '%字号%' 匹配全家族（福州海康威视…）。
  B. 「我要采购120台摄像头」—— 落 fallback。修复：registry 路由 + intent 确定性
     product_demand 检测 + queries 语义检索（robust 同义词）兜底 LIKE。
"""

from __future__ import annotations

import pytest

from price_engine import alias
from price_engine.alias import resolve_company_families, strip_region_prefix
from price_engine.intent import _extract_product_demand, detect_product_demand
from price_engine.models import HardFilters, SearchIntent
from price_engine.guards import has_valid_query_entity
from price_engine.rendering.answer_templates import render_answer
from price_engine.rendering.output_templates import _apply_output_template, get_template

import price_engine.queries as queries_mod
import price_engine.reference as reference_mod


def _intent(**hf):
    return SearchIntent(hard_filters=HardFilters(**hf))


# ═════════════════════════════════════════════════════════
# A. 字号族解析
# ═════════════════════════════════════════════════════════

class TestStripRegionPrefix:
    def test_hangzhou_hikvision(self):
        assert strip_region_prefix("杭州海康威视") == "海康威视"

    def test_no_region(self):
        assert strip_region_prefix("海康威视") == "海康威视"

    def test_zhongguo_not_region(self):
        assert strip_region_prefix("中国移动") == "中国移动"


class TestResolveCompanyFamilies:
    def test_valid_full_name_unchanged(self, monkeypatch):
        # 已是全称 → 不动，保持精确匹配
        monkeypatch.setattr(alias, "_confirm_family_exists", lambda s: True)
        intent = _intent(successful_bidder="杭州海康威视数字技术股份有限公司")
        assert resolve_company_families(intent) == ""
        assert intent.hard_filters.successful_bidder == "杭州海康威视数字技术股份有限公司"
        assert intent.resolved_families == {}
        assert has_valid_query_entity(intent) is True

    def test_short_name_resolves_to_family(self, monkeypatch):
        monkeypatch.setattr(alias, "_confirm_family_exists", lambda s: True)
        intent = _intent(successful_bidder="杭州海康威视")
        desc = resolve_company_families(intent)
        assert "海康威视" in desc
        assert intent.resolved_families == {"successful_bidder": "海康威视"}
        assert intent.hard_filters.successful_bidder == "海康威视"
        # 解析后守卫放行（此前 False → 引导）
        assert has_valid_query_entity(intent) is True

    def test_unconfirmed_short_name_unchanged(self, monkeypatch):
        # 确认不到真实企业 → 不解析，守卫仍拦截（防幻觉）
        monkeypatch.setattr(alias, "_confirm_family_exists", lambda s: False)
        intent = _intent(successful_bidder="采购机器")
        assert resolve_company_families(intent) == ""
        assert intent.resolved_families == {}
        assert has_valid_query_entity(intent) is False

    def test_multiple_short_names_resolved(self, monkeypatch):
        monkeypatch.setattr(alias, "_confirm_family_exists", lambda s: True)
        intent = _intent(successful_bidder="浙江中控", company_name="浙江中控")
        resolve_company_families(intent)
        assert intent.resolved_families.get("successful_bidder") == "中控"
        assert intent.resolved_families.get("company_name") == "中控"


# ═════════════════════════════════════════════════════════
# B. 采购需求检测
# ═════════════════════════════════════════════════════════

class TestExtractProductDemand:
    def test_after_quantity(self):
        assert _extract_product_demand("我要采购120台摄像头") == "摄像头"

    def test_before_quantity(self):
        # 数量在前的写法不能把数量数字混进产品名
        assert _extract_product_demand("采购摄像头120台") == "摄像头"

    def test_with_trailing_usage(self):
        assert _extract_product_demand("采购120台监控摄像头用于校园安防") == "监控摄像头"

    def test_server(self):
        assert _extract_product_demand("采购50台服务器大概多少钱") == "服务器"

    def test_not_demand_no_quantity(self):
        assert _extract_product_demand("采购方式有哪些") is None

    def test_no_verb(self):
        assert _extract_product_demand("120台摄像头价格") is None


class TestDetectProductDemand:
    def test_sets_product_demand(self):
        intent = SearchIntent(hard_filters=HardFilters(), sub_route="all", query_type="mixed")
        assert detect_product_demand(intent, "我要采购120台摄像头") is True
        assert intent.sub_route == "bidding_query"
        assert intent.query_type == "product_demand"
        assert intent.hard_filters.product_name == "摄像头"
        assert intent.semantic_keywords[0] == "摄像头"

    def test_non_demand_unchanged(self):
        intent = SearchIntent(hard_filters=HardFilters(), sub_route="all", query_type="mixed")
        assert detect_product_demand(intent, "杭州海康威视中标了哪些项目") is False
        assert intent.query_type == "mixed"


class TestGuardProductDemand:
    def test_product_name_is_valid_entity(self):
        intent = _intent(product_name="摄像头")
        intent.query_type = "product_demand"
        assert has_valid_query_entity(intent) is True

    def test_product_demand_without_name_invalid(self):
        intent = _intent()
        intent.query_type = "product_demand"
        assert has_valid_query_entity(intent) is False


# ═════════════════════════════════════════════════════════
# 采购需求查询（语义优先 + LIKE 兜底）
# ═════════════════════════════════════════════════════════

class TestProductDemandQuery:
    def test_semantic_path_converts_records(self, monkeypatch):
        monkeypatch.setattr(
            reference_mod, "search_reference",
            lambda settings, text, **kw: {
                "projects": [
                    {
                        "announcement_id": "t1", "project_no": "P1",
                        "project_name": "校园安防监控项目", "purchaser": "某中学",
                        "region": "广东省", "winning_amount_wan": 12.5,
                        "winning_date": "2026-09-11", "source_url": "https://ccgp.gov.cn/1",
                        "score": 0.8,
                        "products": [{"product_name": "监控摄像机", "quantity": "120", "unit": "台"}],
                    }
                ],
            },
        )
        intent = _intent(product_name="摄像头")
        intent.query_type = "product_demand"
        intent.original_question = "我要采购120台摄像头"

        result = queries_mod._query_product_demand(intent)
        assert result["records"][0]["winning_amount"] == 125000.0  # 12.5万 → 元
        assert result["records"][0]["project_name"] == "校园安防监控项目"
        assert result["records"][0]["subject_matter"] == "监控摄像机(120台)"
        assert result["records"][0]["product_name"] == "摄像头"
        assert result["records"][0]["source_url"] == "https://ccgp.gov.cn/1"

    def test_reference_to_bidding_conversion(self):
        from core.config.settings import Settings

        rec = queries_mod._reference_project_to_bidding(
            {"project_name": "X", "winning_amount_wan": 3.5, "products": [], "region": "北京"},
            "服务器", Settings(),
        )
        assert rec["winning_amount"] == 35000.0
        # 无产品 → subject_matter 兜底为项目名
        assert rec["subject_matter"] == "X"

    def test_semantic_empty_falls_back_to_like(self, monkeypatch):
        # 语义空 → LIKE 兜底（mock 连接返回 2 条）
        monkeypatch.setattr(reference_mod, "search_reference", lambda settings, text, **kw: {"projects": []})

        class _Cur:
            def execute(self, sql, params=()):
                self._sql = sql

            def fetchall(self):
                return [
                    {"project_name": "甲项目", "winning_amount": 800000.0,
                     "winning_date": "2026-09-01", "province": "安徽省",
                     "source_url": "https://ccgp.gov.cn/a", "subject_matter": "摄像头×10"},
                    {"project_name": "乙项目", "winning_amount": 900000.0,
                     "winning_date": "2026-09-02", "province": "浙江省",
                     "source_url": "https://ccgp.gov.cn/b", "subject_matter": "监控×20"},
                ]

        class _Conn:
            def cursor(self, cursor=None):
                class _Ctx:
                    def __enter__(s):
                        return _Cur()

                    def __exit__(s, *a):
                        return False

                return _Ctx()

        monkeypatch.setattr(queries_mod, "_get_connection", lambda db: _Conn())
        monkeypatch.setattr(queries_mod, "_release_connection", lambda c: None)
        intent = _intent(product_name="摄像头")
        intent.query_type = "product_demand"
        result = queries_mod._query_product_demand(intent)
        assert result["total_found"] == 2
        assert result["records"][0]["project_name"] == "甲项目"

    def test_no_product_returns_empty(self):
        intent = _intent()
        intent.query_type = "product_demand"
        result = queries_mod._query_product_demand(intent)
        assert result["records"] == []


class TestProductDemandRendering:
    def test_output_template_and_answer(self):
        intent = _intent(product_name="摄像头")
        intent.query_type = "product_demand"
        records = [
            {
                "project_name": "校园安防监控项目", "winning_amount": 125000.0,
                "winning_date": "2026-09-11", "province": "广东省",
                "source_url": "https://ccgp.gov.cn/1", "purchaser": "某中学",
                "subject_matter": "监控摄像机(120台)", "product_name": "摄像头",
            }
        ]
        template = get_template("bidding_query", "product_demand")
        assert template is not None
        output = _apply_output_template(records, intent, template)
        assert [c["key"] for c in output.columns] == [
            "project_name", "purchaser", "winning_amount", "winning_date",
            "province", "subject_matter", "source_url",
        ]
        answer = render_answer("product_demand", records, entity="摄像头")
        assert "摄像头" in answer
        assert "125,000.00 元" in answer

    def test_empty_answer_guides(self):
        answer = render_answer("product_demand", [], entity="摄像头")
        assert "摄像头" in answer
        assert "更通用的产品名称" in answer
