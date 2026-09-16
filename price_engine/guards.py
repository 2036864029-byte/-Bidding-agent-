"""price 引擎共享守卫（W1-3）— 节点子图与工具入口共用同一套判定。

背景（审计 C7）：`price_engine/tools.py::price_query` 与
`agent/nodes/price_inquiry/subgraph.py` 是同一能力的两个入口，但守卫只写在子图里，
tools 入口会绕过 P0-11 实体校验与 P0-12 项目编号纠偏 → 同一问题两条路结果不同
（一个给引导语，一个无脑查全表）。

本模块只提供**判定与归一化**（纯函数，不依赖 agent 层）；路由出口 / 引导话术 /
指标埋点仍由调用方决定。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .intent import (
    _extract_project_number_candidate,
    _is_valid_company_name,
    _looks_like_code,
)
from .models import SearchIntent

logger = logging.getLogger(__name__)

# 查询意图关键词：区分"裸实体名"与"真查询"
QUERY_INTENT_KEYWORDS = re.compile(
    r"查|问|是?什么|哪些|哪个|如何|怎么|多少|有无|有没有|"
    r"查询|了解|看看|帮忙|请问|推荐|帮我|"
    r"中标|招标|采购|投标|发包|工商|不良|处罚|违法|黑名单|拉黑|"
    r"详情|记录|历史|介绍|信息|情况|"
    r"谁|法定代表人|注册资本|股东|经营范围|"
    r"中标情况|中标历史|不良记录|工商信息|经营范围"
)


def has_valid_query_entity(intent: SearchIntent) -> bool:
    """P0-11：是否提取到有效的查询实体。

    合法实体：project_number（含字母+数字）或通过工商主体名称格式校验的
    company_name / purchaser / successful_bidder。未通过校验的实体名视为无效。
    supplier_recommend 例外：行业（hard_filters.industry）按行业过滤查询，
    是合法实体（非全表扫）；其余路由行业不算实体。
    字号族例外（2026-09-16）：`resolved_families` 命中的字段已由
    alias.resolve_company_families 确认为真实企业字号（公司简称），视为合法实体。
    """
    hf = intent.hard_filters
    if hf.project_number and _looks_like_code(hf.project_number):
        return True
    if hf.company_name and _is_valid_company_name(hf.company_name):
        return True
    if hf.purchaser and _is_valid_company_name(hf.purchaser):
        return True
    if hf.successful_bidder and _is_valid_company_name(hf.successful_bidder):
        return True
    # 字号族解析命中 → 合法实体（公司简称，如 杭州海康威视→海康威视）
    for field in ("company_name", "purchaser", "successful_bidder"):
        if field in intent.resolved_families:
            return True
    # product_demand：采购需求（产品名过滤查询，非全表扫）→ 合法实体
    if intent.query_type == "product_demand" and hf.product_name:
        return True
    # supplier_recommend：行业是合法实体（WHERE industry=… 过滤查询，非全表扫）
    if intent.query_type == "supplier_recommend" and hf.industry:
        return True
    # aggregation：有界聚合（ORDER BY + LIMIT），无实体也合法（非全表扫）
    if intent.query_type == "aggregation" and intent.aggregation:
        return True
    return False


def normalize_project_number(intent: SearchIntent, question: str) -> Optional[str]:
    """P0-12：项目编号纠偏 / 注入（就地修改 intent）。返回动作描述或 None。

    1) 已提取到有效编号但 query_type 不是 project_detail → 强制修正；
    2) 未提取到但原文含编号格式 → 确定性扫描注入。
    """
    hf = intent.hard_filters

    if hf.project_number and _looks_like_code(hf.project_number):
        if intent.query_type != "project_detail":
            intent.query_type = "project_detail"
            intent.sub_route = "bidding_query"
            return f"corrected:{hf.project_number}"
        return None

    extracted = _extract_project_number_candidate(question)
    if extracted:
        hf.project_number = extracted
        intent.query_type = "project_detail"
        intent.sub_route = "bidding_query"
        return f"injected:{extracted}"
    return None


__all__ = [
    "QUERY_INTENT_KEYWORDS",
    "has_valid_query_entity",
    "normalize_project_number",
]
