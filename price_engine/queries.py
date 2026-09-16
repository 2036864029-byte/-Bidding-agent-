"""业务专用查询 — 公司/处罚/招标/聚合/全表五条查询路径。"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import pymysql

from core.rdb.mysql_pool import (
    _CLEAN_DB,
    _get_connection,
    _get_settings,
    _release_connection,
)

from .models import SearchIntent
from .recall import _clean_result_row, _query_tables
from .sql_builders import _build_order_clause

logger = logging.getLogger(__name__)

def _query_penalty_by_company_name(company_name: str) -> list[dict[str, Any]]:
    """P0-1：直接用公司名查询 company_penalty 表（不依赖 company_info）。

    用于 penalty_check 查询，避免目标企业不在 company_info 中时无法获取处罚记录。
    P0-11 修复：使用精确匹配替代 LIKE 模糊匹配，杜绝无差别无脑召回。
    """
    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        return []
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """SELECT * FROM `company_penalty`
                   WHERE `company_name` = %s
                   ORDER BY `penalty_date` DESC
                   LIMIT 50""",
                (company_name,),
            )
            rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            clean_row = _clean_result_row(row)
            clean_row["_source_db"] = _CLEAN_DB
            clean_row["_source_table"] = "company_penalty"
            results.append(clean_row)
        return results
    except Exception as e:
        logger.debug("直接查询 company_penalty 失败: %s", e)
        return []
    finally:
        _release_connection(conn)

def _query_company_data(intent: SearchIntent) -> dict[str, Any]:
    """公司信息查询：company_info + company_penalty 双路查询。

    penalty_check 查询时优先直接查 company_penalty（按 company_name），
    避免依赖 company_info 中可能不存在的目标企业。
    """
    hf = intent.hard_filters

    # ── P0-1：penalty_check 直接查 company_penalty ──
    if intent.need_penalty_check:
        target_company = hf.company_name or (
            intent.exact_tokens[0] if intent.exact_tokens else None
        )
        if target_company:
            direct_penalty = _query_penalty_by_company_name(target_company)
            if direct_penalty:
                logger.info(
                    "[PENALTY_DIRECT] 直接查 company_penalty 命中 %d 条 (company=%s)",
                    len(direct_penalty), target_company[:30],
                )
                return {
                    "records": direct_penalty,
                    "total_found": len(direct_penalty),
                    "queried_tables": [f"{_CLEAN_DB}.company_penalty"],
                    "sql_count": 1,
                    "total_sql_time": 0.0,
                }

    # ── 原有逻辑：company_info 主查询 + credit_code 联查 ──
    tables = ["company_info"]
    result = _query_tables(tables, intent)

    # ── supplier_recommend：按 top_n 截断（"推荐几家"），兜底 LIMIT 200 ──
    # 意图已提取 top_n（钳制 [1,20]）；无显式数量时给合理默认（推荐语义）。
    if intent.query_type == "supplier_recommend" and result.get("records"):
        n = intent.top_n or 10
        result["records"] = result["records"][:n]
        result["total_found"] = len(result["records"])

    # ── P0-1b：penalty_check 精确匹配过滤 ──
    # 当直接查 company_penalty 无结果时，降级到 company_info 路径。
    # 但语义召回可能返回无关企业，必须过滤保留目标企业。
    if intent.need_penalty_check and hf.company_name and result["records"]:
        target = hf.company_name
        matched = [rec for rec in result["records"] if rec.get("company_name") == target]
        if not matched:
            logger.info(
                "[PENALTY_FILTER] company_info 中未找到精确匹配 '%s'，返回空结果", target[:30]
            )
            result["records"] = []
        else:
            result["records"] = matched

    # 条件联查 company_penalty
    if intent.need_penalty_check and result["records"]:
        credit_codes = set()
        for rec in result["records"]:
            cc = rec.get("credit_code")
            if cc and cc not in ("", "None", "未提供"):
                credit_codes.add(cc)

        if credit_codes:
            penalty_results: list[dict] = []
            conn = _get_connection(_CLEAN_DB)
            if conn:
                try:
                    with conn.cursor(pymysql.cursors.DictCursor) as cur:
                        for cc in credit_codes:
                            try:
                                cur.execute(
                                    "SELECT * FROM `company_penalty` WHERE `credit_code` = %s ORDER BY `penalty_date` DESC",
                                    (cc,)
                                )
                                for row in cur.fetchall():
                                    clean_row = _clean_result_row(row)
                                    clean_row["_source_db"] = _CLEAN_DB
                                    clean_row["_source_table"] = "company_penalty"
                                    penalty_results.append(clean_row)
                            except Exception as e:
                                logger.debug("联查 company_penalty 失败: %s", e)
                finally:
                    _release_connection(conn)

            # 合并 penalty 数据到主结果
            if penalty_results:
                # 将 penalty 数据按 credit_code 合并到对应 company_info 记录
                penalty_by_cc: dict[str, list[dict]] = {}
                for pr in penalty_results:
                    cc = pr.get("credit_code", "")
                    if cc:
                        penalty_by_cc.setdefault(cc, []).append(pr)

                merged_records = []
                for rec in result["records"]:
                    cc = rec.get("credit_code", "")
                    penalties = penalty_by_cc.get(cc, [])
                    if penalties:
                        # 将第一个 penalty 记录的关键字段合并到 company_info 记录
                        p = penalties[0]
                        rec["penalty_date"] = p.get("penalty_date", "")
                        rec["illegal_behavior"] = p.get("illegal_behavior", "")
                        rec["penalty_result"] = p.get("penalty_result", "")
                        rec["law_enforcement_unit"] = p.get("law_enforcement_unit", "")
                    merged_records.append(rec)

                result["records"] = merged_records
                result["queried_tables"].append(f"{_CLEAN_DB}.company_penalty")
            else:
                # 企业存在但无任何处罚记录 → 返回空（走"暂无不良记录"路径）。
                # 否则会用处罚模板渲染无处罚字段的 company_info 行 → KeyError 崩溃。
                result["records"] = []

    return result

def _query_bidding_data(intent: SearchIntent) -> dict[str, Any]:
    """招投标历史交易查询：bid_project。"""
    # 采购需求：读 bidding 库产品明细 + 中标公告（相似项目金额参考）
    if intent.query_type == "product_demand":
        return _query_product_demand(intent)

    # 聚合查询特殊处理
    if intent.aggregation:
        aggregation_result = _query_bidding_aggregation(intent)
        if aggregation_result:
            return aggregation_result

    return _query_tables(["bid_project"], intent)


def _query_product_demand(intent: SearchIntent) -> dict[str, Any]:
    """采购需求查询：按产品需求返回历史相似中标项目金额参考。

    两级策略（2026-09-16 实测数据稀疏：product_item 仅 1489 条，「摄像头」字面无命中，
    摄像机/监控才有）：
      1. **语义检索优先**：复用 `reference.search_reference`（Milvus 公告集合），
         对同义词/变体稳健（摄像头→摄像机/监控/安防）；
      2. **确定性 LIKE 兜底**：语义失败/空时，按产品名查 product_item + announcement。

    记录统一按 bid_project 形状映射（winning_amount=元、winning_date=公告日、
    province=地区、subject_matter=产品清单），复用 bidding_query 输出/回答模板。
    """
    product = intent.hard_filters.product_name
    empty = {
        "records": [], "total_found": 0, "queried_tables": [],
        "sql_count": 0, "total_sql_time": 0.0,
    }
    if not product:
        return empty

    settings = _get_settings()
    # ── 1. 语义检索优先（robust 到同义词/变体）──
    try:
        from .reference import search_reference

        ref_result = search_reference(settings, intent.original_question, top_k=10)
        projects = ref_result.get("projects") or []
        if projects:
            records = [
                _reference_project_to_bidding(p, product, settings)
                for p in projects
            ]
            return {
                **empty,
                "records": records,
                "total_found": len(records),
                "queried_tables": [
                    f"{settings.mysql_bidding_db}.project_announcement_semantic"
                ],
                "sql_count": 1,
                "total_sql_time": 0.0,
            }
    except Exception as exc:  # noqa: BLE001  语义链路失败（隧道/Milvus）降级 LIKE
        logger.warning("[PRODUCT_DEMAND] 语义参考失败，走确定性 LIKE 兜底: %s", exc)

    # ── 2. 确定性 LIKE 兜底（产品词精确命中）──
    return _query_product_item_like(intent, product, settings, empty)


def _reference_project_to_bidding(project: dict[str, Any], product: str, settings: Any) -> dict[str, Any]:
    """语义参考项目 → bid_project 形状记录（winning_amount 元、subject_matter 产品清单）。"""
    products = project.get("products") or []
    subject = "；".join(
        f"{x.get('product_name') or ''}"
        + (f"({x.get('quantity')}{x.get('unit') or ''})" if x.get("quantity") else "")
        for x in products
        if x.get("product_name")
    )
    return {
        "project_name": project.get("project_name"),
        "winning_amount": round(float(project.get("winning_amount_wan") or 0) * 10000, 2),
        "winning_date": project.get("winning_date"),
        "province": project.get("region"),
        "purchaser": project.get("purchaser"),
        "source_url": project.get("source_url"),
        "subject_matter": subject or project.get("project_name") or "",
        "product_name": product,
        "_source_db": settings.mysql_bidding_db,
        "_source_table": "announcement+project_product_item",
    }


def _query_product_item_like(
    intent: SearchIntent,
    product: str,
    settings: Any,
    empty: dict[str, Any],
) -> dict[str, Any]:
    """确定性 LIKE 兜底：product_item.normalized_product_name / product_name 前缀包含产品词。"""
    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        logger.warning("[PRODUCT_DEMAND] 无法连接 %s 库", settings.mysql_bidding_db)
        return empty

    try:
        like = f"%{product}%"
        sql = """
            SELECT a.project_name,
                   a.purchaser_name AS purchaser,
                   a.award_amount AS winning_amount,
                   a.publish_time AS winning_date,
                   a.region_name AS province,
                   a.source_url,
                   a.announcement_id,
                   GROUP_CONCAT(
                       CONCAT_WS(' ',
                           COALESCE(p.normalized_product_name, p.product_name),
                           CASE WHEN p.quantity IS NOT NULL AND p.quantity <> ''
                                THEN CONCAT('×', p.quantity, COALESCE(p.unit, '')) END)
                       SEPARATOR '；') AS subject_matter
            FROM `project_product_item` p
            JOIN `announcement` a ON a.announcement_id = p.announcement_id
            WHERE p.normalized_product_name LIKE %s OR p.product_name LIKE %s
            GROUP BY a.announcement_id, a.project_name, a.purchaser_name,
                     a.award_amount, a.publish_time, a.region_name, a.source_url
            ORDER BY a.award_amount DESC
            LIMIT 20
        """
        total_sql_time = 0.0
        rows: list[dict] = []
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            start = time.perf_counter()
            cur.execute(sql, (like, like))
            total_sql_time = time.perf_counter() - start
            rows = cur.fetchall()

        results: list[dict] = []
        for row in rows:
            clean = _clean_result_row(row)
            clean["_source_db"] = settings.mysql_bidding_db
            clean["_source_table"] = "announcement+project_product_item"
            clean["product_name"] = product  # 回答模板 multi 头用
            results.append(clean)

        logger.info(
            "[PRODUCT_DEMAND] product=%s rows=%d sql_time=%.3fs",
            product, len(results), total_sql_time,
        )
        return {
            **empty,
            "records": results,
            "total_found": len(results),
            "queried_tables": [f"{settings.mysql_bidding_db}.project_product_item"],
            "sql_count": 1,
            "total_sql_time": total_sql_time,
        }
    except Exception as exc:  # noqa: BLE001  查询失败按空结果降级
        logger.warning("[PRODUCT_DEMAND] 查询失败: %s", exc)
        return empty
    finally:
        _release_connection(conn)

def _query_bidding_aggregation(intent: SearchIntent) -> Optional[dict[str, Any]]:
    """竞价聚合查询（跳过 FULLTEXT，直接走聚合 SQL）。"""
    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        return None

    hf = intent.hard_filters
    family = intent.resolved_families or {}
    conditions: list[str] = []
    params: list[Any] = []

    if hf.successful_bidder:
        fam = family.get("successful_bidder")
        if fam:
            conditions.append("`successful_bidder` LIKE %s")
            params.append(f"%{fam}%")
        else:
            conditions.append("`successful_bidder` = %s")
            params.append(hf.successful_bidder)
    if hf.purchaser:
        fam = family.get("purchaser")
        if fam:
            conditions.append("`purchaser` LIKE %s")
            params.append(f"%{fam}%")
        else:
            conditions.append("`purchaser` = %s")
            params.append(hf.purchaser)
    if hf.province:
        # 地区类（P0 优化 5.2.3：LIKE 前缀模糊匹配，兼容简称/全称差异）
        conditions.append("`province` LIKE %s")
        params.append(f"{hf.province}%")
    if hf.time_range:
        if hf.time_range.get("start"):
            conditions.append("`winning_date` >= %s")
            params.append(hf.time_range["start"])
        if hf.time_range.get("end"):
            conditions.append("`winning_date` <= %s")
            params.append(hf.time_range["end"])
    if hf.project_stage:
        # 状态类（P0 优化 5.2.3：LIKE 前缀模糊匹配）
        conditions.append("`project_stage` LIKE %s")
        params.append(f"{hf.project_stage}%")
    if hf.winning_amount_range:
        if hf.winning_amount_range.get("min") is not None:
            conditions.append("`winning_amount` >= %s")
            params.append(hf.winning_amount_range["min"])
        if hf.winning_amount_range.get("max") is not None:
            conditions.append("`winning_amount` <= %s")
            params.append(hf.winning_amount_range["max"])

    top_n = intent.top_n or 1
    order_clause = _build_order_clause(intent)
    if order_clause == "ORDER BY `_score_` DESC":
        # B1：本 SQL 的 select 列表没有 _score_ 列（聚合走的是字段列表而非召回列），
        # 默认排序会触发 MySQL 1054 并被下方 except 吞掉 → 静默回退普通检索。
        order_clause = "ORDER BY `winning_amount` DESC"

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    try:
        sql = (
            f"SELECT project_name, project_number, purchaser, successful_bidder, "
            f"winning_amount, winning_date, subject_matter, agent, project_stage, "
            f"project_category, province, city, publish_date "
            f"FROM `bid_project` "
            f"WHERE {where_clause} "
            f"{order_clause} "
            f"LIMIT {top_n}"
        )

        total_sql_time = 0.0
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            start = time.perf_counter()
            cur.execute(sql, tuple(params))
            total_sql_time = time.perf_counter() - start
            rows = cur.fetchall()

        if not rows:
            return None

        results: list[dict] = []
        queried_tables = [f"{_CLEAN_DB}.bid_project"]
        for row in rows:
            clean_row = _clean_result_row(row)
            clean_row["_source_db"] = _CLEAN_DB
            clean_row["_source_table"] = "bid_project"
            results.append(clean_row)

        logger.info(
            "[AGGREGATION] aggregation=%s top_n=%d rows=%d sql_time=%.3fs",
            intent.aggregation, top_n, len(results), total_sql_time,
        )

        return {
            "records": results,
            "total_found": len(results),
            "queried_tables": queried_tables,
            "sql_count": 1,
            "total_sql_time": total_sql_time,
            "aggregation": {
                "aggregation_type": intent.aggregation,
                "top_n": top_n,
            },
        }
    except Exception as e:
        logger.warning("聚合查询失败: %s", e)
        return None
    finally:
        _release_connection(conn)

def _query_all_tables(intent: SearchIntent) -> dict[str, Any]:
    """all 兜底模式：遍历 3 张核心表（product_info 已下线）。"""
    return _query_tables(
        ["company_info", "company_penalty", "bid_project"],
        intent,
    )
