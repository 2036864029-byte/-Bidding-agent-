# 功能：补全 bidding.announcement.source_url —— file:// 本地路径 → 可访问的 ccgp 公告页。
"""source_url 补全脚本（方案 §1.1）。

背景：`announcement.source_url` 语义 = 对应公告的具体网页链接。实测 802 条里
292 条是真 http 公告页，510 条存成了本地 file:// 路径（爬虫没保存网页 URL，
`crawler_raw_payload.url` 也全空）。

本脚本把 510 条 file:// 行探测成 web 链接并**直接回写 source_url**：
  - 候选公式：https://www.ccgp.gov.cn/cggg/{zygg|dfgg}/zbgg/<YYYYMM>/t<ID>.htm
    （<YYYYMM> = announcement_id 第 2~7 位）
  - zygg/dfgg 段用探测判定：先试 zygg（实测绝大多数 file:// 行命中），200 即
    采用；否则试 dfgg；都失败则保留原值（可重跑续补）。
  - 本地文件路径信息不丢：`announcement.raw_file` 列仍持有原路径。

用法：
  .venv\\Scripts\\python.exe scripts/backfill_announcement_source_url.py          # 全量补全
  .venv\\Scripts\\python.exe scripts/backfill_announcement_source_url.py --limit 10  # 试点 N 条
  .venv\\Scripts\\python.exe scripts/backfill_announcement_source_url.py --concurrency 4
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings
from core.rdb.mysql_pool import _get_connection, _release_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_source_url")

# 候选 URL 模板：zygg=中央公告 / dfgg=地方公告，YYYYMM = announcement_id 第 2~7 位
URL_TEMPLATE = "https://www.ccgp.gov.cn/cggg/{seg}/zbgg/{yyyymm}/{aid}.htm"
PROBE_TIMEOUT = 12.0


def _candidates(aid: str) -> list[str]:
    yyyymm = aid[1:7]
    return [
        URL_TEMPLATE.format(seg="zygg", yyyymm=yyyymm, aid=aid),
        URL_TEMPLATE.format(seg="dfgg", yyyymm=yyyymm, aid=aid),
    ]


def _fetch_rows(conn, limit: Optional[int]) -> list[tuple[str, str]]:
    """取 (announcement_id, source_url)，只挑 file:// 行（http 行本就是 web 链接）。"""
    sql = (
        "SELECT announcement_id, source_url FROM announcement "
        "WHERE source_url LIKE 'file://%' ORDER BY announcement_id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(sql)
        return [(r[0], r[1]) for r in cur.fetchall()]


def _probe_one(
    aid: str,
    session: requests.Session,
    retries: int = 2,
) -> Optional[str]:
    """探测两个候选，返回 200 的 URL；都失败返回 None。

    失败（网络错误 / 404 / 5xx）不抛出——补全是尽力而为，失败行保留原值。
    """
    for url in _candidates(aid):
        for attempt in range(1, retries + 1):
            try:
                resp = session.get(url, stream=True, timeout=PROBE_TIMEOUT, allow_redirects=True)
                # stream 模式下不拉正文，拿到状态码即关闭连接
                resp.close()
                if resp.status_code == 200:
                    return url
                if resp.status_code in (403, 429):
                    # 疑似被限流/拦截：退避重试，别连环触发
                    time.sleep(1.0 * attempt)
                    continue
                break  # 明确失败（404/5xx），无需重试同 URL
            except Exception as exc:  # noqa: BLE001  网络层错误按尽力而为处理
                logger.debug("%s %s 第 %d 次尝试异常: %s", aid, url, attempt, exc)
                time.sleep(0.4 * attempt)
    return None


def _backfill(conn, rows: list[tuple[str, str]], concurrency: int) -> dict:
    """并发探测并回写。返回 {total, filled, failed} 计数。"""
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/126 Safari/537.36"
    )
    session.headers["Accept-Language"] = "zh-CN,zh;q=0.9"

    filled: dict[str, str] = {}  # aid -> web url
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(_probe_one, aid, session): aid for aid, _ in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            aid = futures[fut]
            url = fut.result()
            if url:
                filled[aid] = url
            if i % 50 == 0 or i == len(rows):
                logger.info("探测进度 %d/%d，已命中 %d", i, len(rows), len(filled))

    # 回写：executemany 批量 UPDATE（不改 raw_file，本地路径仍可查）
    if filled:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE announcement SET source_url=%s, updated_at=updated_at "
                "WHERE announcement_id=%s",
                [(url, aid) for aid, url in filled.items()],
            )
        conn.commit()

    return {"total": len(rows), "filled": len(filled), "failed": len(rows) - len(filled)}


def main() -> int:
    parser = argparse.ArgumentParser(description="补全 announcement.source_url（file:// → web 链接）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（试点用）")
    parser.add_argument("--concurrency", type=int, default=6, help="并发探测线程数（默认 6）")
    args = parser.parse_args()

    settings = Settings()
    conn = _get_connection(settings.mysql_bidding_db)
    if conn is None:
        logger.error("无法连接 bidding 库")
        return 1

    try:
        rows = _fetch_rows(conn, args.limit)
        logger.info("待补全 file:// 行数: %d", len(rows))
        if not rows:
            return 0

        t0 = time.time()
        stats = _backfill(conn, rows, args.concurrency)
        logger.info(
            "补全完成: 处理 %d / 命中 %d / 未命中 %d，耗时 %.1fs",
            stats["total"], stats["filled"], stats["failed"], time.time() - t0,
        )
        if stats["failed"]:
            logger.warning("未命中 %d 条（保留原 file:// 值，可稍后重跑续补）", stats["failed"])
        return 0
    finally:
        _release_connection(conn)


if __name__ == "__main__":
    sys.exit(main())
