"""price 5 级召回链 → RecallStage 适配（方案 Phase 3 / D-6）。

core/retrieval 提供通用熔断状态机（RecallPipeline / RecallStage 协议）；
price 特有的 SQL 阶段适配放本模块，避免 core → price_engine 反向依赖
（core/retrieval/stages.py 为通用占位，见该文件 docstring）。

阶段与 _RECALL_STAGE_WEIGHTS 对应：
  1=fulltext_or、3=like_fallback、4=split_keyword、5=full_scan(is_scan)。
Milvus 语义召回（stage 0）由 _query_tables 先行执行，不属于本链。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from core.rdb.mysql_pool import _CLEAN_DB
from core.retrieval.base import RecallStage

from .recall import _execute_sql_with_timeout, _get_settings
from .models import SearchIntent
from .sql_builders import (
    _build_candidate_sql,
    _build_exact_constraint_sql,
    _build_full_scan_sql,
    _build_like_fallback_sql,
)

logger = logging.getLogger(__name__)


def _effective_timeout(deadline_ms: int) -> float:
    """阶段内单条 SQL 超时 = min(全局单 SQL 超时, 剩余预算)，保底 0.5s。

    让全局 deadline 真正约束到阶段内的每条 SQL，而不是只在阶段之间检查。
    """
    settings = _get_settings()
    budget = (deadline_ms or 0) / 1000.0
    if budget <= 0:
        return 0.5
    return max(0.5, min(float(settings.sql_query_timeout), budget))


class _SqlRecallStage(RecallStage):
    """单条 SQL 召回阶段：构建 SQL → 带超时执行 → 命中即返回行。"""

    def __init__(
        self,
        *,
        name: str,
        stage_no: int,
        table_name: str,
        classification: Dict[str, List[str]],
        intent: SearchIntent,
        build_sql,
        is_scan: bool = False,
        stats: Dict[str, Any],
    ) -> None:
        self.name = name
        self.stage_no = stage_no
        self.is_scan = is_scan
        self._table_name = table_name
        self._classification = classification
        self._intent = intent
        self._build_sql = build_sql
        self._stats = stats

    def run(
        self, ctx: Dict[str, Any], *, deadline_ms: int
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        # 构建器签名统一为 (table_name, classification, intent)
        sql_tuple = self._build_sql(self._table_name, self._classification, self._intent)
        if sql_tuple is None:
            return False, []
        try:
            rows, elapsed = _execute_sql_with_timeout(
                ctx["conn"], sql_tuple[0], sql_tuple[1],
                self._table_name, self.name,
                timeout=_effective_timeout(deadline_ms),
            )
        except Exception as e:
            if "fulltext" in str(e).lower():
                logger.warning(
                    "[FULLTEXT_MISSING] db=%s table=%s: %s",
                    _CLEAN_DB, self._table_name, e,
                )
            else:
                logger.debug(
                    "检索链阶段 %s 查询失败 %s.%s: %s",
                    self.name, _CLEAN_DB, self._table_name, e,
                )
            return False, []

        self._stats["sql_count"] += 1
        self._stats["sql_time"] += elapsed
        if rows:
            for row in rows:
                row["_recall_stage_"] = self.stage_no
            logger.info(
                "[RECALL_CHAIN] table=%s stage=%s rows=%d",
                self._table_name, self.name, len(rows),
            )
            return True, rows
        return False, []


class _SplitKeywordStage(RecallStage):
    """逐关键词拆分重试（stage 4）：对每个关键词依次 FULLTEXT + LIKE 重试合并去重。"""

    name = "split_keyword"
    is_scan = False

    def __init__(
        self,
        *,
        table_name: str,
        classification: Dict[str, List[str]],
        intent: SearchIntent,
        stats: Dict[str, Any],
    ) -> None:
        self._table_name = table_name
        self._classification = classification
        self._intent = intent
        self._stats = stats

    def _collect(self, ctx: Dict[str, Any], rows, seen_ids, split_rows, deadline_ms):
        """把一次查询命中行去重并入 split_rows。"""
        for row in rows:
            row_id = str(row.get("_id_", ""))
            if row_id and row_id not in seen_ids:
                row["_recall_stage_"] = 4
                split_rows.append(row)
                seen_ids.add(row_id)

    def run(
        self, ctx: Dict[str, Any], *, deadline_ms: int
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        keywords = [kw for kw in self._intent.semantic_keywords if kw]
        if len(keywords) <= 1:
            return False, []

        split_rows: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        timeout = _effective_timeout(deadline_ms)

        for kw in keywords[:3]:
            per_kw_intent = replace(self._intent, semantic_keywords=[kw])

            retry_sql = _build_candidate_sql(
                self._table_name, self._classification, per_kw_intent,
                include_exact_tokens=False,
            )
            if retry_sql is not None:
                try:
                    rows, elapsed = _execute_sql_with_timeout(
                        ctx["conn"], retry_sql[0], retry_sql[1],
                        self._table_name, f"FULLTEXT_RETRY_{kw}", timeout=timeout,
                    )
                except Exception as e:
                    logger.debug(
                        "逐关键词 FULLTEXT 重试失败 %s.%s: %s",
                        _CLEAN_DB, self._table_name, e,
                    )
                else:
                    self._stats["sql_count"] += 1
                    self._stats["sql_time"] += elapsed
                    self._collect(ctx, rows, seen_ids, split_rows, deadline_ms)

            like_retry = _build_like_fallback_sql(
                self._table_name, self._classification, per_kw_intent,
            )
            if like_retry is not None:
                try:
                    rows, elapsed = _execute_sql_with_timeout(
                        ctx["conn"], like_retry[0], like_retry[1],
                        self._table_name, f"LIKE_RETRY_{kw}", timeout=timeout,
                    )
                except Exception as e:
                    logger.debug(
                        "逐关键词 LIKE 重试失败 %s.%s: %s",
                        _CLEAN_DB, self._table_name, e,
                    )
                else:
                    self._stats["sql_count"] += 1
                    self._stats["sql_time"] += elapsed
                    self._collect(ctx, rows, seen_ids, split_rows, deadline_ms)

        if split_rows:
            logger.info(
                "[RECALL_CHAIN] table=%s stage=SPLIT_KEYWORD rows=%d",
                self._table_name, len(split_rows),
            )
        return bool(split_rows), split_rows


def build_recall_stages(
    table_name: str,
    classification: Dict[str, List[str]],
    intent: SearchIntent,
    stats: Dict[str, Any],
) -> List[RecallStage]:
    """构造 price 5 级召回链的 RecallStage 序列（full_scan 默认 is_scan 禁入）。

    前置 exact_constraint（stage 0）：有代码类精确 ID（project_number/credit_code）
    时纯约束直查，命中即短路——修复"项目编号存在却按编号查不到"的假阴性
    （语义阶段在采购人/投标人列搜编号必然 0）。
    """
    return [
        _SqlRecallStage(
            name="exact_constraint", stage_no=0,
            table_name=table_name, classification=classification, intent=intent,
            build_sql=_build_exact_constraint_sql, stats=stats,
        ),
        _SqlRecallStage(
            name="fulltext_or", stage_no=1,
            table_name=table_name, classification=classification, intent=intent,
            build_sql=_build_candidate_sql, stats=stats,
        ),
        _SqlRecallStage(
            name="like_fallback", stage_no=3,
            table_name=table_name, classification=classification, intent=intent,
            build_sql=_build_like_fallback_sql, stats=stats,
        ),
        _SplitKeywordStage(
            table_name=table_name, classification=classification,
            intent=intent, stats=stats,
        ),
        _SqlRecallStage(
            name="full_scan", stage_no=5,
            table_name=table_name, classification=classification, intent=intent,
            build_sql=_build_full_scan_sql, is_scan=True, stats=stats,
        ),
    ]
