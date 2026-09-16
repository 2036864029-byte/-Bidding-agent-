"""项目概况相似检索与金额参考 —— POST /inquiry/reference 的查询层（方案 §5/§6）。

独立新链路（与 price_engine / 公共检索完全并列，纯增量）：用户粘贴一段招标项目
概况 → BGE-M3 向量化 → Milvus 集合 `project_announcement_semantic` 相似检索 →
MySQL `bidding` 库回表（主信息 + 产品明细）→ 金额统一万元 → 询价页右栏参考。

设计要点（方案 §7 金额口径）：
  - `winning_amount_wan` / `amount_stats.*_wan` 一律**万元**（award_amount 元 / 1e4）；
  - 产品 `unit_price_yuan` 保留**元**（Phase 3 既定口径，字段名即单位）；
  - 默认 `award_amount > 1000`（排除 ≤1000 元脏行），`min_amount_wan` 只可上调；
  - `source_url` 已是补全后的具体公告页 web 链接（scripts/backfill_announcement_source_url.py）。

返回的是 snake_case 原始形状（与 /kanban/overview 分层一致），前端 adapters 归一化。
"""

from __future__ import annotations

import logging
import re
import statistics
from typing import Any, Optional

from pymilvus import MilvusClient

from core.config.settings import Settings
from core.embeddings import get_embeddings
from core.rdb.mysql_pool import _get_connection, _release_connection
from core.vectorstore.index_params import build_dense_search_params
from core.vectorstore.namespace import PROJECT_ANNOUNCEMENT_COLLECTION

logger = logging.getLogger(__name__)

# 输入下限（方案 §6：text <10 字 → 400）
MIN_TEXT_CHARS = 10
# 金额下限（元）：排除 ≤1000 元的 4 条脏行
DEFAULT_MIN_AMOUNT = 1000.0
# 默认返回条数
DEFAULT_TOP_K = 10
# 余弦相似度下限（方案 §12：0.30 起步，沿用语义召回阈值，上线后按金标校准）
SCORE_THRESHOLD = 0.30
# 检索上限（比 top_k 多取一点，供金额过滤后仍有余量）
RETRIEVE_OVERFETCH = 2

# unit_price 是自由文本：可能含多值（中文逗号/顿号分隔）、千分位、或纯空。
# 取第一段可解析的数值作为单价。
_PRICE_SEP_RE = re.compile(r"[，,、;；\s]+")


def _milvus_client(settings: Settings) -> MilvusClient:
    return MilvusClient(
        uri=settings.resolved_milvus_uri,
        timeout=settings.milvus_timeout,
        token=settings.milvus_token or None,
    )


def _collection_ready(client: MilvusClient) -> bool:
    """集合存在且非空（方案 §6：未索引 → 503）。"""
    if not client.has_collection(PROJECT_ANNOUNCEMENT_COLLECTION):
        return False
    stats = client.get_collection_stats(PROJECT_ANNOUNCEMENT_COLLECTION)
    return int(stats.get("row_count", 0) or 0) > 0


def _retrieve(
    client: MilvusClient,
    embeddings: Any,
    settings: Settings,
    text: str,
    top_k: int,
    region: Optional[str],
) -> list[tuple[str, float]]:
    """向量检索 → [(announcement_id, score)]，按分数降序，滤掉阈值以下。

    region 走 Milvus 标量过滤（集合冗余存了 region 字段，方案 §3），
    避免回表后再过滤导致 top-K 空档。
    """
    vector = embeddings.embed_query(text)
    expr = None
    if region:
        escaped = region.replace('"', '\\"')
        expr = f'region == "{escaped}"'
    search_params = build_dense_search_params(settings)  # HNSW → COSINE + ef
    raw = client.search(
        PROJECT_ANNOUNCEMENT_COLLECTION,
        data=[vector],
        anns_field="vector",
        search_params=search_params,
        limit=top_k * RETRIEVE_OVERFETCH,
        filter=expr,
        output_fields=["announcement_id"],
    )[0]

    hits: list[tuple[str, float]] = []
    for hit in raw:
        score = float(hit.score or 0.0)
        if score < SCORE_THRESHOLD:
            continue
        aid = str(hit.entity.get("announcement_id") or "")
        if aid:
            hits.append((aid, score))
    return hits


def _fetch_announcements(conn, ids: list[str], min_amount: float) -> list[tuple]:
    """回表①：公告主信息（含原文链接），金额过滤在 SQL 里做。"""
    marks = ",".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT announcement_id, project_no, project_name, purchaser_name,
                       region_name, award_amount, publish_time, source_url
                  FROM announcement
                 WHERE announcement_id IN ({marks}) AND award_amount > %s""",
            ids + [min_amount],
        )
        return cur.fetchall()


def _fetch_products(conn, ids: list[str]) -> dict[str, list[dict]]:
    """回表②：产品明细按公告聚合（unit_price DESC 只是粗略序，展示端自排序）。"""
    if not ids:
        return {}
    marks = ",".join(["%s"] * len(ids))
    result: dict[str, list[dict]] = {aid: [] for aid in ids}
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT announcement_id, product_name, normalized_product_name, brand,
                       model, quantity, unit, unit_price
                  FROM project_product_item
                 WHERE announcement_id IN ({marks})
                 ORDER BY announcement_id, item_id""",
            ids,
        )
        for aid, pname, nname, brand, model, qty, unit, uprice in cur.fetchall():
            if not pname:
                continue
            result[aid].append(
                {
                    "product_name": pname,
                    "normalized_product_name": nname,
                    "brand": brand,
                    "model": model,
                    "quantity": qty,
                    "unit": unit,
                    "unit_price_yuan": _parse_price(uprice),
                }
            )
    return result


def _parse_price(value: Any) -> Optional[int | float]:
    """unit_price 自由文本 → 数值；解析不了返回 None。

    '152000' → 152000；'1,586,000.00' → 1586000（先去千分位再整体解析）；
    '601990.00，326869.00' → 601990.0（整体解析失败 → 取首段可解析值）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # 单值最常见：去掉千分位后整体解析（若含中文逗号会拼成非法数字 → 走分段）
    try:
        num = float(text.replace(",", "").replace("，", ""))
    except ValueError:
        num = None
    if num is not None:
        return int(num) if num == int(num) else round(num, 2)
    # 多值/混合文本：取首段可解析数值
    for part in _PRICE_SEP_RE.split(text):
        part = part.strip().replace(",", "")
        if not part:
            continue
        try:
            num = float(part)
        except ValueError:
            continue
        return int(num) if num == int(num) else round(num, 2)
    return None


def _amount_stats(amounts_wan: list[float]) -> Optional[dict]:
    """金额统计（万元）。空列表 → None（前端显示空态）。"""
    if not amounts_wan:
        return None
    return {
        "min_wan": round(min(amounts_wan), 2),
        "median_wan": round(statistics.median(amounts_wan), 2),
        "max_wan": round(max(amounts_wan), 2),
        "avg_wan": round(statistics.mean(amounts_wan), 2),
        "sample_count": len(amounts_wan),
    }


def search_reference(
    settings: Settings,
    text: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_amount_wan: Optional[float] = None,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """项目概况 → 相似中标项目 + 金额参考。

    Raises:
        ValueError: text 为空或 <10 字（端点映射 400）。
        LookupError: 集合未就绪 / bidding 库连不上（端点映射 503）。
    """
    text = (text or "").strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError("请提供更完整的项目概况（至少 10 字）")

    client = _milvus_client(settings)
    if not _collection_ready(client):
        raise LookupError("项目概况知识库未初始化，请先运行 scripts/build_announcement_index.py")

    hits = _retrieve(
        client, get_embeddings(settings), settings, text,
        min(max(1, top_k), 50), region,
    )
    if not hits:
        return {"query": text, "total": 0, "amount_stats": None, "projects": []}

    # 金额下限：默认排除 ≤1000 元脏行；min_amount_wan 只可上调
    floor = DEFAULT_MIN_AMOUNT
    if min_amount_wan and min_amount_wan > 0:
        floor = max(floor, min_amount_wan * 10000)

    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        raise LookupError("bidding 库连接失败，请稍后重试")
    try:
        rows = _fetch_announcements(conn, [aid for aid, _ in hits], floor)
        products = _fetch_products(conn, [r[0] for r in rows])
    finally:
        _release_connection(conn)

    score_by_id = dict(hits)
    projects = []
    for r in rows:
        aid = r[0]
        projects.append(
            {
                "announcement_id": aid,
                "project_no": r[1],
                "project_name": r[2],
                "purchaser": r[3],
                "region": r[4],
                "winning_amount_wan": round(float(r[5] or 0) / 10000, 2),
                "winning_date": r[6].isoformat()[:10] if r[6] else None,
                "source_url": r[7],
                "score": round(score_by_id.get(aid, 0.0), 3),
                "products": products.get(aid, []),
            }
        )
    projects.sort(key=lambda p: p["score"], reverse=True)

    return {
        "query": text,
        "total": len(projects),
        "amount_stats": _amount_stats([p["winning_amount_wan"] for p in projects]),
        "projects": projects,
    }
