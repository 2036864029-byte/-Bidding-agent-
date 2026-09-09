"""price_engine tools 门面 — price_query() / price_query_as_tool()（方案 Phase 3 / D-8）。

为多 Agent 复用提供结构化查询工具：自然语言问题 → 记录列表。

W1-3：与 `agent/nodes/price_inquiry/subgraph.py` 共用 `price_engine.guards` 的
守卫判定（P0-12 编号纠偏/注入 + P0-11 实体校验）。守卫不通过时**显式拒绝**
（返回空记录 + `guard_reason`），不再无脑查全表——避免同一问题在两条入口给出
不同结果（审计 C7）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .enum_norm import _normalize_intent_enums
from .guards import QUERY_INTENT_KEYWORDS, has_valid_query_entity, normalize_project_number
from .intent import _build_llm, _looks_like_code, _parse_unified_intent, _safe_parse_intent
from .queries import _query_all_tables, _query_bidding_data, _query_company_data

logger = logging.getLogger(__name__)

_SUB_ROUTE_QUERY_FN: Dict[str, Any] = {
    "company_query": _query_company_data,
    "bidding_query": _query_bidding_data,
    "all": _query_all_tables,
}

_EMPTY_RESULT: Dict[str, Any] = {
    "records": [], "total_found": 0, "queried_tables": [],
    "sql_count": 0, "total_sql_time": 0.0,
    "truncated": False, "total_returned": 0,
}


def price_query(question: str, *, max_rows: Optional[int] = None) -> Dict[str, Any]:
    """自然语言结构化业务查询 → 记录列表（价格/标讯引擎工具门面）。

    Args:
        question: 用户查询（如「XX公司的工商信息」「XX中标了哪些项目」）。
        max_rows: 返回 records 行数上限（None=不限制；>0 时生效，W3-3 工具契约）。
            截断时 ``truncated=True``、``total_returned`` 为截断前条数；
            ``total_found`` 始终为全量命中数，便于 agent 感知还有更多。

    Returns:
        {"records": [...], "total_found": int, "queried_tables": [...],
         "sql_count": int, "total_sql_time": float,
         "truncated": bool, "total_returned": int}
        守卫拦截时额外带 ``guard_reason``（records 为空）。
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
        return {**_EMPTY_RESULT, "notice": "product 功能线已下线"}

    # ── W1-3：与子图共用守卫 ──────────────────────────────────
    normalize_project_number(intent, question)
    hf = intent.hard_filters
    has_entity = has_valid_query_entity(intent)
    has_intent_kw = bool(QUERY_INTENT_KEYWORDS.search(question))
    has_project_number = bool(hf.project_number and _looks_like_code(hf.project_number))

    def _refuse(reason: str) -> Dict[str, Any]:
        logger.info("[QUERY_GUARD] tools 入口拦截 reason=%s question=%.80s", reason, question)
        return {**_EMPTY_RESULT, "guard_reason": reason}

    if intent.query_type == "project_detail" and not hf.project_number:
        return _refuse("project_detail_no_number")
    if has_entity and not has_intent_kw and not has_project_number:
        return _refuse("bare_entity_no_intent")
    if intent.sub_route in ("bidding_query", "company_query", "all") and not has_entity:
        return _refuse("no_entity_for_route")

    query_fn = _SUB_ROUTE_QUERY_FN.get(intent.sub_route, _query_all_tables)
    result = query_fn(intent)
    if max_rows is not None:
        limit = max(1, int(max_rows))
        records = result.get("records") or []
        result = {
            **result,
            "truncated": len(records) > limit,
            "total_returned": len(records),
            "records": records[:limit],
        }
    return result


class _PriceQueryArgs(BaseModel):
    question: str = Field(description="用户结构化业务查询问题（企业/处罚/中标情报）")
    max_rows: int = Field(10, description="返回记录行数上限（默认 10；total_found 仍为全量命中数）")


def price_query_as_tool() -> StructuredTool:
    """langchain tool 包装（P6 多 Agent / W3-3 Deep Agent 用）。"""

    def _func(question: str, max_rows: int = 10) -> dict:
        return price_query(question, max_rows=max_rows)

    return StructuredTool.from_function(
        name="price_query",
        description=(
            "结构化业务数据查询：企业工商信息、企业不良记录/处罚、招投标中标情报。"
            "输入自然语言问题，返回结构化记录列表。"
        ),
        func=_func,
        args_schema=_PriceQueryArgs,
    )
