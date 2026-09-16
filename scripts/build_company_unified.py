"""构建统一公司表 `bidding.company_unified` —— 供应商模块的**单一读取面**。

## 为什么要这张表（以及我上一轮错在哪）

上一轮我把交付包里的 `bidding.supplier`（1,058 行、8 天）当成"供应商表"，
于是得出"行业字段撑不住、工商信息连不上"的结论 —— **那是错的**。

项目里本来就有**工商档案表** `ztb_clean.company_info`（**38,911 行**，99%+ 覆盖）：
province / industry（95 类）/ legal_person / 注册资本 / 成立日期 / 经营状态 / 地址 / 经营范围，
且 `source_file='bidding_project_company_info.csv'` —— 它就是从招投标项目里抽出来的。
实测它与 `bid_project` 精确匹配 **10,455 行**，覆盖旧标讯 8,631 个中标人中的 **4,133 个（48%）**。

所以：**项目侧我合并了（project_unified），公司侧没合并** —— 这张表就是补上欠的那一半。

## 两个来源

| source | 来源 | 行数 | 工商信息 |
|---|---|---|---|
| `LEGACY` | `ztb_clean.company_info` | 38,911 | 全（~99%） |
| `CCGP` | `bidding.supplier` | 1,058 | 无（只有名称 + 37.7% 信用代码） |

中标业绩**统一**从 `project_unified` 汇总（不分来源），按公司名归一化后匹配 ——
这样两家都出现过的公司（实测 69 家）也能拿到完整业绩，不会各算各的。

⚠️ 一个计量约定：`project_unified` 里一行可能**并列多个中标人**（用 `、` 串联），
而该行的 `winning_amount` 是**总额**。故按中标人数**均摊**金额，避免同一笔钱记三次。
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings

# 归一化：去「（已除名）（待清理）」这类前缀 + 去空白。**不做**去「有限公司」后缀——
# 实测那样只会把命中从 102 提到 118，却可能把两家不同公司并成一家，得不偿失。
_PREFIX_RE = re.compile(r"^[（(][^）)]*[）)]")
_SPLIT_RE = re.compile(r"[、,，;；]")
_SUPPLIER_HDR_RE = re.compile(r"^供应商名称[、,，]?\s*")


def norm_name(s: str) -> str:
    if not s:
        return ""
    return _PREFIX_RE.sub("", s).strip().replace(" ", "").replace("　", "")


def split_winners(raw: str) -> list[str]:
    """把「供应商名称、甲公司、乙公司」拆成 ['甲公司','乙公司']。"""
    if not raw:
        return []
    body = _SUPPLIER_HDR_RE.sub("", raw)
    return [x.strip() for x in _SPLIT_RE.split(body) if len(x.strip()) >= 4]


DDL = """
CREATE TABLE IF NOT EXISTS company_unified (
  company_id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source                VARCHAR(16)  NOT NULL COMMENT 'LEGACY=company_info / CCGP=bidding.supplier',
  src_id                VARCHAR(64)  NOT NULL COMMENT '原表主键',
  company_name          VARCHAR(255) NOT NULL,
  normalized_name       VARCHAR(255) NOT NULL COMMENT '用于匹配的归一化名',
  credit_code           VARCHAR(32)  DEFAULT NULL,
  province              VARCHAR(64)  DEFAULT NULL COMMENT '注册地（CCGP 侧无此数据）',
  city                  VARCHAR(64)  DEFAULT NULL,
  district              VARCHAR(64)  DEFAULT NULL,
  industry              VARCHAR(128) DEFAULT NULL COMMENT '国标行业分类，95 类（CCGP 侧无）',
  legal_person          VARCHAR(128) DEFAULT NULL,
  registered_capital    VARCHAR(64)  DEFAULT NULL COMMENT '原文串，如 500万人民币',
  registered_capital_cny DECIMAL(20,2) DEFAULT NULL,
  establish_date        DATE         DEFAULT NULL,
  business_status       VARCHAR(64)  DEFAULT NULL,
  company_type          VARCHAR(128) DEFAULT NULL,
  company_level         VARCHAR(32)  DEFAULT NULL,
  address               VARCHAR(512) DEFAULT NULL,
  business_scope        TEXT         DEFAULT NULL,
  trade_types           VARCHAR(255) DEFAULT NULL COMMENT '仅 CCGP：交易类型',
  win_count             INT          NOT NULL DEFAULT 0,
  win_amount_total      DECIMAL(20,2) NOT NULL DEFAULT 0,
  first_win_date        DATE         DEFAULT NULL,
  last_win_date         DATE         DEFAULT NULL,
  main_region           VARCHAR(64)  DEFAULT NULL COMMENT '中标最多的省份（≠注册地）',
  PRIMARY KEY (company_id),
  UNIQUE KEY uk_cu_src (source, src_id),
  KEY idx_cu_name (normalized_name(191)),
  KEY idx_cu_province (province),
  KEY idx_cu_industry (industry),
  KEY idx_cu_credit (credit_code),
  KEY idx_cu_wins (win_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

COLS = (
    "source", "src_id", "company_name", "normalized_name", "credit_code",
    "province", "city", "district", "industry", "legal_person",
    "registered_capital", "registered_capital_cny", "establish_date",
    "business_status", "company_type", "company_level", "address", "business_scope",
    "trade_types", "win_count", "win_amount_total", "first_win_date", "last_win_date",
    "main_region",
)

# 公司 ↔ 项目 的关联表：把"谁中了哪个项目"真正落库，
# 否则详情页每次都要拿名字去全表模糊匹配（18k 行 × 每请求）。
LINK_DDL = """
CREATE TABLE IF NOT EXISTS company_win (
  source         VARCHAR(16)  NOT NULL,
  src_id         VARCHAR(64)  NOT NULL,
  project_no     VARCHAR(128) NOT NULL,
  project_name   VARCHAR(512) DEFAULT NULL,
  region         VARCHAR(64)  DEFAULT NULL,
  category       VARCHAR(128) DEFAULT NULL,
  winning_amount DECIMAL(20,2) DEFAULT NULL,
  winning_date   DATE         DEFAULT NULL,
  PRIMARY KEY (source, src_id, project_no),
  KEY idx_cw_project (project_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

LINK_COLS = ("source", "src_id", "project_no", "project_name", "region",
             "category", "winning_amount", "winning_date")


def build_win_index(cur) -> dict:
    """`project_unified` → {归一化公司名: 业绩 + 关联项目}。多中标人按人数均摊金额。"""
    cur.execute(
        "SELECT project_no, project_name, category, winning_bidder, winning_amount, "
        "       winning_date, region FROM project_unified"
    )
    idx: dict[str, dict] = {}
    for pno, pname, cat, bidder, amount, wdate, region in cur.fetchall():
        names = split_winners(bidder)
        if not names:
            continue
        share = (float(amount) / len(names)) if amount else 0.0
        for nm in names:
            k = norm_name(nm)
            if not k:
                continue
            e = idx.setdefault(k, {"n": 0, "amt": 0.0, "first": None, "last": None,
                                   "regions": collections.Counter(), "projects": []})
            e["n"] += 1
            e["amt"] += share
            if wdate:
                e["first"] = wdate if e["first"] is None else min(e["first"], wdate)
                e["last"] = wdate if e["last"] is None else max(e["last"], wdate)
            if region:
                e["regions"][region] += 1
            if pno:
                e["projects"].append((pno, pname, region, cat, amount, wdate))
    return idx


def _link_rows(source: str, src_id: str, w: dict | None) -> list[tuple]:
    if not w:
        return []
    return [(source, src_id, pno, pname, region, cat, amt, wdate)
            for pno, pname, region, cat, amt, wdate in w["projects"]]


def legacy_rows(cur, win: dict) -> tuple[list[tuple], list[tuple]]:
    cur.execute(
        "SELECT id, company_name, legal_person, registered_capital, "
        "registered_capital_amount_cny, establish_date, business_status, province, city, "
        "district, industry, company_type, credit_code, address, business_scope, company_level "
        "FROM ztb_clean.company_info"
    )
    out, links = [], []
    for (cid, name, legal, cap, cap_cny, est, status, prov, city, dist,
         ind, ctype, credit, addr, scope, level) in cur.fetchall():
        w = win.get(norm_name(name)) if name else None
        main = w["regions"].most_common(1)[0][0] if w and w["regions"] else None
        sid = str(cid)
        out.append((
            "LEGACY", sid, name or "", norm_name(name), credit, prov, city, dist,
            ind, legal, cap, cap_cny, est, status, ctype, level, addr, scope,
            None, w["n"] if w else 0, round(w["amt"], 2) if w else 0,
            w["first"] if w else None, w["last"] if w else None, main,
        ))
        links.extend(_link_rows("LEGACY", sid, w))
    return out, links


def ccgp_rows(cur, win: dict) -> tuple[list[tuple], list[tuple]]:
    cur.execute(
        "SELECT supplier_id, normalized_company_name, company_name, credit_code, "
        "rank_trade_types, source FROM bidding.supplier"
    )
    out, links = [], []
    for (sid, norm, name, credit, trades, src) in cur.fetchall():
        key = norm_name(norm or name)
        w = win.get(key) if key else None
        main = w["regions"].most_common(1)[0][0] if w and w["regions"] else None
        ssid = str(sid)
        out.append((
            # CCGP 侧没有工商信息：province/industry/法人都留空，如实标 null
            "CCGP", ssid, name or norm or "", key or (name or norm or ""), credit,
            None, None, None, None, None, None, None, None, None, None, None, None, None,
            trades, w["n"] if w else 0, round(w["amt"], 2) if w else 0,
            w["first"] if w else None, w["last"] if w else None, main,
        ))
        links.extend(_link_rows("CCGP", ssid, w))
    return out, links


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 company_unified 统一公司表")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import pymysql

    s = Settings()
    conn = pymysql.connect(host=s.mysql_host, port=s.mysql_port, user=s.mysql_user,
                           password=s.mysql_password, database=s.mysql_bidding_db,
                           charset="utf8mb4", autocommit=False)
    cur = conn.cursor()

    win = build_win_index(cur)
    print(f"中标业绩索引: {len(win)} 家公司（来自 project_unified）")

    if not args.dry_run:
        cur.execute(DDL)
        cur.execute(LINK_DDL)
        conn.commit()

    for src_name, builder in (("LEGACY", legacy_rows), ("CCGP", ccgp_rows)):
        rows, links = builder(cur, win)
        print(f"  {src_name}: {len(rows)} 家, 关联项目 {len(links)} 条")
        if args.dry_run:
            continue
        cur.execute("DELETE FROM company_unified WHERE source=%s", (src_name,))
        cur.execute("DELETE FROM company_win WHERE source=%s", (src_name,))
        ph = ",".join(["%s"] * len(COLS))
        for i in range(0, len(rows), 500):
            cur.executemany(
                f"INSERT INTO company_unified ({','.join(COLS)}) VALUES ({ph})", rows[i:i + 500]
            )
        lph = ",".join(["%s"] * len(LINK_COLS))
        for i in range(0, len(links), 500):
            cur.executemany(
                f"INSERT IGNORE INTO company_win ({','.join(LINK_COLS)}) VALUES ({lph})",
                links[i:i + 500],
            )
        conn.commit()

    if args.dry_run:
        conn.close()
        return 0

    cur.execute(
        "SELECT source, COUNT(*), SUM(province IS NOT NULL), SUM(industry IS NOT NULL), "
        "       SUM(win_count>0) FROM company_unified GROUP BY source"
    )
    print("\n源 / 行数 / 有省份 / 有行业 / 有中标:")
    for r in cur.fetchall():
        print("  %-8s %6d  %8d %8d %8d" % r)
    cur.execute("SELECT COUNT(*), SUM(win_count>0) FROM company_unified")
    tot, wins = cur.fetchone()
    print(f"合计 {tot} 家，其中有中标业绩 {wins} 家")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
