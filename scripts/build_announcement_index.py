# 功能：把 bidding.announcement 建进 Milvus 集合 project_announcement_semantic（项目概况相似检索）。
"""招标公告语义索引脚本（方案 §3/§4）。

数据：`bidding.announcement`（802 条中标公告）∪ `project_product_item`（产品明细）。
集合：`project_announcement_semantic`，HNSW + COSINE，pk = announcement_id（802/802 唯一）。

被向量化的文本构成（决定检索质量，见方案 §3）：
    {project_name}
    {产品需求清单：normalized_product_name 逗号拼接，无产品则省略}
    {content_text 截断前 1800 字}
项目名+产品清单放最前（语义最像"产品需求"），正文只取头段——bge-m3 安全截断
上限 2000 字符，中位 2660 字全文会被硬截，故主动截 1800 保信号密度。

用法：
  .venv\\Scripts\\python.exe scripts/build_announcement_index.py          # 增量 upsert（按 announcement_id 幂等）
  .venv\\Scripts\\python.exe scripts/build_announcement_index.py --rebuild # 删库重建（schema 变更时）
  .venv\\Scripts\\python.exe scripts/build_announcement_index.py --dry-run # 只看条数与抽样文本，不写库

前置依赖：Milvus（MILVUS_URI，v26 栈 19531）、模型隧道（EMBEDDING_BASE_URL）、MySQL bidding 库。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pymysql
from pymilvus import DataType, MilvusClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings
from core.embeddings import get_embeddings
from core.rdb.mysql_pool import _get_connection, _release_connection
from core.vectorstore.namespace import PROJECT_ANNOUNCEMENT_COLLECTION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_announcement_index")

# 正文截断字数（bge-m3 安全截断上限 2000，这里留余量给项目名+产品清单）
TEXT_TAIL_LIMIT = 1800
# 批大小：每批 100 条 embed（与 mysql_price_semantic 的 MYSQL_SEMANTIC_BATCH_SIZE 一致）
BATCH_SIZE = 100

# 索引参数（方案 §3 显式指定：与全站默认 128 不同，此集合按方案定为 200）
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
SEARCH_EF = 64


def _announcement_rows(conn: pymysql.Connection) -> list[dict]:
    """全量公告：announcement_id / project_no / project_name / region / content_text。"""
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            "SELECT announcement_id, project_no, project_name, region_name, "
            "content_text FROM announcement ORDER BY announcement_id"
        )
        return [dict(r) for r in cur.fetchall()]


def _product_names_by_announcement(conn: pymysql.Connection, ids: list[str]) -> dict[str, list[str]]:
    """announcement_id -> normalized_product_name 列表（保持数量，去空值）。"""
    if not ids:
        return {}
    marks = ",".join(["%s"] * len(ids))
    result: dict[str, list[str]] = {aid: [] for aid in ids}
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT announcement_id, normalized_product_name FROM project_product_item "
            f"WHERE announcement_id IN ({marks})",
            ids,
        )
        for aid, name in cur.fetchall():
            if name and name.strip():
                result[aid].append(str(name).strip())
    return result


def _build_doc_text(project_name: str | None, products: list[str], content: str | None) -> str:
    """按方案 §3 组句：项目名 + 产品清单 + 正文头段。"""
    parts: list[str] = []
    if project_name and project_name.strip():
        parts.append(str(project_name).strip())
    if products:
        parts.append("、".join(products[:20]))  # 产品清单最多拼 20 项防文本爆炸
    if content:
        head = str(content).strip()[:TEXT_TAIL_LIMIT]
        if head:
            parts.append(head)
    return "\n".join(parts)


def _create_collection(client: MilvusClient, settings: Settings) -> None:
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("announcement_id", DataType.VARCHAR, max_length=128, is_primary=True)
    schema.add_field("project_no", DataType.VARCHAR, max_length=128)
    schema.add_field("region", DataType.VARCHAR, max_length=64)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dim)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": HNSW_M, "efConstruction": HNSW_EF_CONSTRUCTION},
    )
    client.create_collection(
        collection_name=PROJECT_ANNOUNCEMENT_COLLECTION,
        schema=schema,
        index_params=index_params,
    )
    client.load_collection(PROJECT_ANNOUNCEMENT_COLLECTION)
    logger.info("集合 %s 已创建并加载", PROJECT_ANNOUNCEMENT_COLLECTION)


def _embed_batch(embeddings, texts: list[str]) -> list[list[float]]:
    """批量向量化，失败即抛（重试由调用方决定）。"""
    return embeddings.embed_documents(texts)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建招标公告语义索引集合")
    parser.add_argument("--rebuild", action="store_true", help="删库重建（schema 变更时）")
    parser.add_argument("--dry-run", action="store_true", help="只看条数与抽样文本，不写库")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    settings = Settings()
    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        logger.error("无法连接 bidding 库")
        return 1

    try:
        rows = _announcement_rows(conn)
        all_ids = [r["announcement_id"] for r in rows]
        products = _product_names_by_announcement(conn, all_ids)
        logger.info("公告数=%d，带产品明细公告数=%d", len(rows), sum(1 for v in products.values() if v))

        texts = [
            _build_doc_text(r["project_name"], products.get(r["announcement_id"], []), r["content_text"])
            for r in rows
        ]

        if args.dry_run:
            logger.info("[dry-run] 不写库，抽样 2 条待向量化文本：")
            for r, t in zip(rows[:2], texts[:2]):
                print(f"--- {r['announcement_id']} ({len(t)} 字符) ---")
                print(t[:200])
            print(f"\n[dry-run] 文本长度分布: "
                  f"min={min(len(t) for t in texts)} max={max(len(t) for t in texts)}")
            return 0

        client = MilvusClient(
            uri=settings.resolved_milvus_uri,
            timeout=settings.milvus_timeout,
            token=settings.milvus_token or None,
        )
        if args.rebuild and client.has_collection(PROJECT_ANNOUNCEMENT_COLLECTION):
            client.drop_collection(PROJECT_ANNOUNCEMENT_COLLECTION)
            logger.info("已删除旧集合 %s（--rebuild）", PROJECT_ANNOUNCEMENT_COLLECTION)
        if not client.has_collection(PROJECT_ANNOUNCEMENT_COLLECTION):
            _create_collection(client, settings)
        else:
            client.load_collection(PROJECT_ANNOUNCEMENT_COLLECTION)

        embeddings = get_embeddings(settings)
        t0 = time.time()
        inserted = 0
        for start in range(0, len(rows), args.batch_size):
            batch = rows[start:start + args.batch_size]
            batch_texts = texts[start:start + args.batch_size]
            vectors = _embed_batch(embeddings, batch_texts)
            payload = [
                {
                    "announcement_id": r["announcement_id"],
                    "project_no": r["project_no"] or "",
                    "region": r["region_name"] or "",
                    "text": t,
                    "vector": list(v),
                }
                for r, t, v in zip(batch, batch_texts, vectors)
            ]
            client.upsert(PROJECT_ANNOUNCEMENT_COLLECTION, payload)
            inserted += len(payload)
            logger.info("upsert 进度 %d/%d（%.1fs）", inserted, len(rows), time.time() - t0)

        client.flush(PROJECT_ANNOUNCEMENT_COLLECTION)
        stats = client.get_collection_stats(PROJECT_ANNOUNCEMENT_COLLECTION)
        logger.info(
            "索引完成: 写入 %d 条，集合 row_count=%s，耗时 %.1fs",
            inserted, stats.get("row_count"), time.time() - t0,
        )
        return 0
    finally:
        _release_connection(conn)


if __name__ == "__main__":
    sys.exit(main())
