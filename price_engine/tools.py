"""price_engine tools 门面 — price_query() / price_query_as_tool()（方案 Phase 3 / D-8）。

为 P6 多 Agent 复用提供结构化查询工具：自然语言问题 → 记录列表。不经 node 守卫/
引导（守卫属节点侧，见 D-5），返回引擎直出结构化结果。本 Phase 仅建门面 + 单测，
不接线（P6 直接挂 agent 用）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .enum_norm import _normalize_intent_enums
from .intent import _build_llm, _parse_unified_intent, _safe_parse_intent
from .queries import _query_all_tables, _query_bidding_data, _query_company_data

logger = logging.getLogger(__name__)

_SUB_ROUTE_QUERY_FN: Dict[str, Any] = {
    "company_query": _query_company_data,
    "bidding_query": _query_bidding_data,
    "all": _query_all_tables,
}


def price_query(question: str) -> Dict[str, Any]:
    """自然语言结构化业务查询 → 记录列表（价格/标讯引擎工具门面）。

    Args:
        question: 用户查询（如「XX公司的工商信息」「XX中标了哪些项目」）。

    Returns:
        {"records": [...], "total_found": int, "queried_tables": [...],
         "sql_count": int, "total_sql_time": float}
    """
    llm = _build_llm()
    intent = _parse_unified_intent(question, llm)
    intent = _safe_parse_intent(intent)
    try:
        intent = _normalize_intent_enums(intent)
    except Exception as e:
        logger.warning("[ENUM_NORM] 枚举归一化异常，降级为原始过滤: %s", e)

    # product 功能线已下线（与节点侧一致的能力边界）
    if intent.sub_route == "product_query":
        return {
            "records": [], "total_found": 0, "queried_tables": [],
            "sql_count": 0, "total_sql_time": 0.0,
            "notice": "product 功能线已下线",
        }

    query_fn = _SUB_ROUTE_QUERY_FN.get(intent.sub_route, _query_all_tables)
    return query_fn(intent)


class _PriceQueryArgs(BaseModel):
    question: str = Field(description="用户结构化业务查询问题（企业/处罚/中标情报）")


def price_query_as_tool() -> StructuredTool:
    """langchain tool 包装（P6 多 Agent 直接挂 agent 用）。"""
    return StructuredTool.from_function(
        name="price_query",
        description=(
            "结构化业务数据查询：企业工商信息、企业不良记录/处罚、招投标中标情报。"
            "输入自然语言问题，返回结构化记录列表。"
        ),
        func=price_query,
        args_schema=_PriceQueryArgs,
    )
