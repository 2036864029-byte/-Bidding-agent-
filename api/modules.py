"""模块只读端点的查询层（看板 / 供应商）。

与 `price_engine` 的分工：那里是**对话式检索**（自由文本 → 多级召回），
这里是**看板式聚合**（固定维度 → 分组计数）。两者共用 `core.rdb.mysql_pool`。

数据来源一律是 `bidding` 库（交付包 11 张表 + 统一宽表 `project_unified`），
**不再读 `ztb_clean`** —— 宽表已经把 2018~2025 的历史切片并进来了。

返回的是**原始形状**（snake_case），归一化交给前端 `adapters/`——
这是项目既有分层（见 `docs/前端接口清单.md` §3.2）。
"""

from __future__ import annotations

from typing import Any

from core.config.settings import Settings
from core.rdb.mysql_pool import _get_connection, _release_connection

# 看板趋势窗口：宽表跨 2018~2026，全量月度有 90+ 点，图上看不了。
HEATMAP_TOP = 40
COMPOSITION_TOP = 12
INDUSTRY_SERIES = 3     # 多线属 all-pairs 图形，dataviz 规范硬约束上限 3 系列

# `trade_stat_fact.region_name` 用**简称**（上海/内蒙古），而 `project_unified.region`
# 用全称（上海市/内蒙古自治区）。热力图取自前者、筛选器取自后者，不统一就会出现
# 「下拉选安徽省、热力图写安徽」这种对不上的情况，且点筛选会把热力图过滤成空。
# 这里把简称归一到全称，让整页口径一致。
_SHORT_PROVINCE_SUFFIX = {
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
    "北京": "北京市", "天津": "天津市", "上海": "上海市", "重庆": "重庆市",
    "香港": "香港特别行政区", "澳门": "澳门特别行政区",
}


def _canonical_province(short: str) -> str:
    s = (short or "").strip()
    if s in _SHORT_PROVINCE_SUFFIX:
        return _SHORT_PROVINCE_SUFFIX[s]
    return s if s.endswith(("省", "自治区", "特别行政区")) else f"{s}省"


def _rows(cur) -> list[tuple]:
    return cur.fetchall()


def build_kanban_overview(
    settings: Settings,
    *,
    region: str | None = None,
    industry: str | None = None,
) -> dict[str, Any]:
    """看板一屏数据。维度：地区（省级）/ 类别 / 月。

    ⚠️ 口径说明（都源自实测的数据特征，不是随便定的）：
      - **热力图与行业走势来自 `trade_stat_fact`**（省级公共资源交易统计），
        不是 `project_unified` —— 后者 65% 压在安徽，画不成分布图。
        行业走势里的"行业"实为**交易类型**（项目表里没有行业字段：
        旧数据只有粗类 `project_category`，新数据 0%）。
      - `composition`（行业占比）用的是宽表 `category`（粒度粗：学校/政府/货物）
      - `recent_projects` = 近 30 天，对新数据（2026-09）有效；历史切片自然为 0
      - **没有产品榜**：产品明细的频率分布无信号（最高 3 次、94% 只出现 1 次），
        任何排序都是并列时按名称排的假象，已于 2026-09-15 撤掉
    """
    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        raise RuntimeError("bidding 库连接失败")

    where, params = [], []
    if region:
        where.append("region = %s")
        params.append(region)
    if industry:
        where.append("category = %s")
        params.append(industry)
    cond = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*),
                           COALESCE(SUM(winning_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)),0),
                           COUNT(DISTINCT region),
                           COUNT(DISTINCT category)
                      FROM project_unified {cond}""",
                params,
            )
            total, recent, regions_n, industries_n = cur.fetchone()

            # 热力/占比**不受筛选影响**（否则选了地区就只剩一格，看不出分布）
            #
            # 地区热力图取自 `trade_stat_fact`（交付包里的**省级交易统计**），
            # 不是 `project_unified` —— 后者 65% 压在安徽（爬虫采集偏向），
            # 画出来是"安徽 + 其他"，看不出地区分布。trade_stat_fact 是
            # 32 省 × 月 × 11 类交易的官方统计，分布可用。
            # 取**最新月**、按省汇总（表内无 ALL 行，直接求和不会重复计数）。
            cur.execute(
                "SELECT MAX(stat_date) FROM trade_stat_fact WHERE metric_code='project_count'"
            )
            latest = cur.fetchone()[0]
            cur.execute(
                "SELECT region_name, SUM(metric_value) v FROM trade_stat_fact "
                "WHERE metric_code='project_count' AND stat_date=%s "
                "GROUP BY region_name ORDER BY v DESC LIMIT %s",
                (latest, HEATMAP_TOP),
            )
            heatmap = [
                {"region": _canonical_province(r), "count": int(v or 0)}
                for r, v in _rows(cur)
            ]
            heatmap_month = latest.isoformat() if latest else None

            cur.execute(
                "SELECT category, COUNT(*) c FROM project_unified "
                "WHERE category IS NOT NULL GROUP BY category ORDER BY c DESC LIMIT %s",
                (COMPOSITION_TOP,),
            )
            comp_raw = _rows(cur)
            comp_total = sum(int(c) for _, c in comp_raw) or 1
            composition = [
                {"industry": k, "count": int(c), "pct": round(100.0 * int(c) / comp_total, 1)}
                for k, c in comp_raw
            ]

            # 行业走势：交易类型 × 月，取自 trade_stat_fact（与热力图同源）。
            # ⚠️ 这里的"行业"实为**交易类型**（工程建设/政府采购/国有产权…），
            # 因为项目表里根本没有行业字段（旧数据只有粗类 project_category，新数据 0%）。
            # 多线图最多 3 条 —— all-pairs 图形的 CVD 硬约束，故取总量前 3。
            cur.execute(
                "SELECT trade_type_name, stat_date, SUM(metric_value) "
                "FROM trade_stat_fact WHERE metric_code='project_count' "
                "GROUP BY trade_type_name, stat_date"
            )
            by_type: dict[str, dict] = {}
            totals: dict[str, float] = {}
            months_set = set()
            for name, d, v in _rows(cur):
                by_type.setdefault(name, {})[d] = float(v or 0)
                totals[name] = totals.get(name, 0.0) + float(v or 0)
                months_set.add(d)
            months = sorted(months_set)
            top = [n for n, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:INDUSTRY_SERIES]]
            industry_trend = {
                "months": [m.strftime("%Y-%m") for m in months],
                "series": [
                    {"name": n, "values": [int(by_type[n].get(m, 0)) for m in months]}
                    for n in top
                ],
            }

            # 筛选器地区**直接取自热力图**（同一来源、同一命名），
            # 否则「下拉是安徽省、图上写安徽」对不上，点筛选还会把热力图滤空。
            regions = [h["region"] for h in heatmap]
            cur.execute(
                "SELECT category FROM project_unified WHERE category IS NOT NULL "
                "GROUP BY category ORDER BY COUNT(*) DESC LIMIT %s",
                (COMPOSITION_TOP,),
            )
            categories = [c for (c,) in _rows(cur)]

            cur.execute("SELECT MAX(updated_at) FROM project")
            updated = cur.fetchone()[0]
    finally:
        _release_connection(conn)

    return {
        "summary": {
            "total_projects": int(total),
            "recent_projects": int(recent),
            "regions": int(regions_n),
            "industries": int(industries_n),
        },
        "filters": {"regions": regions, "industries": categories, "categories": categories},
        "heatmap": heatmap,
        # 热力图取自哪个月（前端要如实标注窗口，不能装作是全量）
        "heatmap_month": heatmap_month,
        "composition": composition,
        "industry_trend": industry_trend,
        "updated_at": updated.isoformat() if updated else None,
    }


# ── 供应商全景（company_unified） ──────────────────────────────────────────
SUPPLIER_PAGE = 60      # 一页返回多少家（39,969 家不可能全发）
SUPPLIER_WINS = 5       # 每家带几条近期中标


def build_supplier_overview(
    settings: Settings,
    *,
    province: str | None = None,
    industry: str | None = None,
    keyword: str | None = None,
    limit: int = SUPPLIER_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """供应商全景：**按地区 / 行业筛选**，返回基本信息 + 中标情况。

    数据源是 `company_unified`（`ztb_clean.company_info` 38,911 家工商档案
    ∪ `bidding.supplier` 新品，其中 **69 家与 LEGACY 同信用代码/同名已跨源合并**
    进 LEGACY 富行，见 `scripts/build_company_unified.py::merge_cross_source`），
    中标业绩来自 `company_win` 关联表。

    ⚠️ 两条必须如实说明的口径：
      1. **工商信息只有 LEGACY 侧有**（省份/行业/法人/注册资本…）。CCGP 侧其余
         公司是交付包里 8 天的新公司，没有工商数据 —— `province`/`industry` 为 null，
         因此**按地区/行业筛选时它们不会出现**（只在"全部"里可见）。
      2. 中标业绩来自名称匹配（归一化后），非权威关联。多中标人已按人数**均摊金额**。

    分页：39,969 家默认按中标次数倒序取前 `limit` 家，`total` 给出符合条件的总数。
    """
    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        raise RuntimeError("bidding 库连接失败")

    where, params = [], []
    if province:
        where.append("province = %s")
        params.append(province)
    if industry:
        where.append("industry = %s")
        params.append(industry)
    if keyword:
        where.append("normalized_name LIKE %s")
        params.append(f"%{keyword}%")
    cond = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM company_unified {cond}", params)
            total = int(cur.fetchone()[0])

            cur.execute(
                f"""SELECT company_id, source, src_id, company_name, credit_code,
                           province, city, district, industry, legal_person,
                           registered_capital, registered_capital_cny, establish_date,
                           business_status, company_type, company_level, address,
                           business_scope, trade_types, win_count, win_amount_total,
                           first_win_date, last_win_date, main_region
                      FROM company_unified {cond}
                     ORDER BY win_count DESC, company_id
                     LIMIT %s OFFSET %s""",
                params + [int(limit), int(offset)],
            )
            rows = cur.fetchall()

            # 一次取完整页的近期中标，避免 N+1
            recent: dict[tuple, list] = {}
            for src in {r[1] for r in rows}:
                ids = [r[2] for r in rows if r[1] == src]
                if not ids:
                    continue
                marks = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"""SELECT source, src_id, project_no, project_name, region, category,
                               winning_amount, winning_date
                          FROM company_win
                         WHERE source=%s AND src_id IN ({marks})
                         ORDER BY winning_date DESC""",
                    [src] + ids,
                )
                for lsrc, lsrcid, pno, pname, reg, cat, amt, wdate in _rows(cur):
                    recent.setdefault((lsrc, lsrcid), [])
                    if len(recent[(lsrc, lsrcid)]) < SUPPLIER_WINS:
                        recent[(lsrc, lsrcid)].append({
                            "project_no": pno,
                            "project_name": pname,
                            "region": reg,
                            "category": cat,
                            "winning_amount": float(amt) if amt is not None else None,
                            "winning_date": wdate.isoformat() if wdate else None,
                        })

            suppliers = []
            for r in rows:
                key = (r[1], r[2])
                suppliers.append({
                    "id": f"{r[1]}:{r[2]}",
                    "source": r[1],
                    "name": r[3],
                    "credit_code": r[4],
                    "province": r[5], "city": r[6], "district": r[7],
                    "industry": r[8],
                    "legal_person": r[9],
                    "registered_capital": r[10],
                    "registered_capital_cny": float(r[11]) if r[11] is not None else None,
                    "establish_date": r[12].isoformat() if r[12] else None,
                    "business_status": r[13],
                    "company_type": r[14],
                    "company_level": r[15],
                    "address": r[16],
                    "business_scope": r[17],
                    "trade_types": r[18],
                    "win_count": int(r[19] or 0),
                    "win_amount_total": float(r[20] or 0),
                    "first_win_date": r[21].isoformat() if r[21] else None,
                    "last_win_date": r[22].isoformat() if r[22] else None,
                    "main_region": r[23],
                    "recent_wins": recent.get(key, []),
                })

            cur.execute(
                "SELECT province FROM company_unified WHERE province IS NOT NULL "
                "GROUP BY province ORDER BY COUNT(*) DESC"
            )
            provinces = [p for (p,) in _rows(cur)]
            cur.execute(
                "SELECT industry FROM company_unified WHERE industry IS NOT NULL "
                "GROUP BY industry ORDER BY COUNT(*) DESC"
            )
            industries = [i for (i,) in _rows(cur)]
    finally:
        _release_connection(conn)

    return {
        "filters": {"provinces": provinces, "industries": industries},
        "total": total,
        "returned": len(suppliers),
        "suppliers": suppliers,
    }
