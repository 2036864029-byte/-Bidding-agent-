"""构建统一宽表 `bidding.project_unified` —— 新旧招投标数据的**单一读取面**。

为什么不让旧数据直接进新 schema：新 schema 是**公告中心**结构（attachment /
project_product_item / award 全部外键挂 announcement_id），而旧 `bid_project`
（2018~2025，17,742 行）**根本没有公告**。硬塞要么造 17,742 条假公告，要么破坏外键。
故另建宽表：新旧各自 ETL 进来，`source` 列标记来源，新 schema 保持原样。

数据事实（2026-09-15 实测）：
  - 旧 ztb_clean.bid_project 17,742 行，中标日期 2018-01-29 ~ 2025-08-03
  - 新 bidding.project        755 行，2026-09-08 ~ 2026-09-15
  - 两者 project_number 交集 = **0** → 互补切片，不冲突，`uk_pu_no` 可安全唯一

两处清洗（都有实测依据）：
  1. **项目名**：旧数据 1,873 条超长，其中 1,705 条（91%）是「真实名（招标项目名称）标段…于…」
     的公告正文拼接 → 在 `（招标项目名称）` 处截断即得真名；余下超长按 200 字截断。
  2. **省级规范化**：新数据 `region_name` 161 个值，仅 58.3% 是标准省名。
     三级兜底：① 本身就是标准省名 → 直用；② 从 `purchaser_address` 里认出省名；
     ③ 查内建城市表。**认不出的（市辖区/长安区/西湖区/南山区/旅游度假区）落「其他」，
     不猜** —— 要 100% 覆盖需要 GB/T 2260 行政区划参考表，属后续。

用法::

    python scripts/build_project_unified.py --dry-run   # 只统计不写库
    python scripts/build_project_unified.py             # 建表 + 灌数
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings

# ── 省级规范化 ────────────────────────────────────────────────────────────
PROVINCES = [
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省", "江苏省", "浙江省",
    "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省", "湖南省",
    "广东省", "海南省", "四川省", "贵州省", "云南省", "陕西省", "甘肃省",
    "青海省", "台湾省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区",
    "香港特别行政区", "澳门特别行政区",
]

# 第三级：内建城市/区/县 → 省。**只收有把握的**；同名的（长安区、西湖区、南山区）
# 一律不收，落「其他」——猜错会让热力图悄悄错位，比留空更糟。
CITY_TO_PROVINCE = {
    "西安市": "陕西省", "深圳市": "广东省", "长沙市": "湖南省", "南京市": "江苏省",
    "广州市": "广东省", "青岛市": "山东省", "厦门市": "福建省", "大连市": "辽宁省",
    "杭州市": "浙江省", "枣庄市": "山东省", "九原区": "内蒙古自治区",
    "达尔罕茂明安联合旗": "内蒙古自治区", "哈尔滨市": "黑龙江省", "郑州市": "河南省",
    "岳麓区": "湖南省", "泗阳县": "江苏省", "武侯区": "四川省", "黄埔区": "广东省",
    "沈阳市": "辽宁省", "淄博市": "山东省", "齐河县": "山东省", "武汉市": "湖北省",
    "昌黎县": "河北省", "杨陵区": "陕西省", "阿拉善左旗": "内蒙古自治区",
    "额济纳旗": "内蒙古自治区", "新疆生产建设兵团": "新疆维吾尔自治区",
    "白山市": "吉林省", "长春市": "吉林省", "德化县": "福建省", "银川市": "宁夏回族自治区",
    "海口市": "海南省", "昌都地区": "西藏自治区", "佛山市": "广东省", "历下区": "山东省",
    "桂林市": "广西壮族自治区", "高邑县": "河北省", "石家庄市": "河北省",
    "南宁市": "广西壮族自治区", "南岗区": "黑龙江省", "香洲区": "广东省",
    "哈密市": "新疆维吾尔自治区", "美兰区": "海南省", "二连浩特市": "内蒙古自治区",
    "云龙区": "江苏省", "南城县": "江西省", "南昌市": "江西省",
    "连云港市开发区": "江苏省", "成县": "甘肃省", "花都区": "广东省",
    "青铜峡市": "宁夏回族自治区", "沾化区": "山东省", "潍坊市": "山东省",
    "鄄城县": "山东省", "济南市": "山东省", "汉阴县": "陕西省", "武昌区": "湖北省",
    "烟台市": "山东省", "滨湖区": "江苏省", "文山市": "云南省", "博乐市": "新疆维吾尔自治区",
    "喀什地区": "新疆维吾尔自治区", "北湖区": "湖南省", "日喀则市": "西藏自治区",
    "日喀则地区": "西藏自治区", "苏尼特左旗": "内蒙古自治区", "珠海市": "广东省",
    "浦东新区": "上海市", "南充市": "四川省", "敦煌市": "甘肃省",
    "乌鲁木齐市": "新疆维吾尔自治区", "新都区": "四川省", "成都市": "四川省",
    "饶平县": "广东省",
}

UNKNOWN_REGION = "其他"

# 项目名里"真实名字"与公告正文的分界标记（实测 91% 的长名靠它切开）
NAME_MARKERS = ("（招标项目名称）", "(招标项目名称)", "（项目名称）", "(项目名称)")
MAX_NAME = 200


def normalize_region(region_name: str | None, address: str | None) -> str:
    """三级兜底 → 标准省名；认不出落「其他」。"""
    rn = (region_name or "").strip()
    if rn in PROVINCES:
        return rn
    addr = address or ""
    for p in PROVINCES:
        if p in addr:
            return p
    if rn in CITY_TO_PROVINCE:
        return CITY_TO_PROVINCE[rn]
    return UNKNOWN_REGION


def clean_project_name(name: str | None) -> str | None:
    """截掉公告正文尾巴，超长再兜底截断。"""
    if not name:
        return name
    n = name
    for mark in NAME_MARKERS:
        i = n.find(mark)
        if i > 0:
            n = n[:i]
            break
    n = n.strip()
    if len(n) > MAX_NAME:
        n = n[:MAX_NAME] + "…"
    return n


DDL = """
CREATE TABLE IF NOT EXISTS project_unified (
  unified_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source          VARCHAR(16)  NOT NULL COMMENT 'LEGACY=ztb_clean.bid_project / CCGP=bidding.project',
  src_id          VARCHAR(64)  NOT NULL COMMENT '原表主键，可回溯',
  project_no      VARCHAR(128) NOT NULL,
  project_name    VARCHAR(512) DEFAULT NULL COMMENT '已清洗：截掉公告正文',
  region          VARCHAR(64)  DEFAULT NULL COMMENT '省级规范化（尽力，认不出=其他）',
  region_raw      VARCHAR(64)  DEFAULT NULL COMMENT '原始地区值，纠错用',
  city            VARCHAR(64)  DEFAULT NULL,
  category        VARCHAR(128) DEFAULT NULL,
  purchaser       VARCHAR(255) DEFAULT NULL,
  agency          VARCHAR(255) DEFAULT NULL,
  winning_bidder  VARCHAR(500) DEFAULT NULL,
  winning_amount  DECIMAL(20,2) DEFAULT NULL,
  winning_date    DATE         DEFAULT NULL,
  budget_amount   DECIMAL(20,2) DEFAULT NULL,
  publish_date    DATE         DEFAULT NULL,
  announcement_id VARCHAR(64)  DEFAULT NULL COMMENT '仅 CCGP 有，用于下钻',
  PRIMARY KEY (unified_id),
  UNIQUE KEY uk_pu_no (project_no),
  KEY idx_pu_source (source),
  KEY idx_pu_region (region),
  KEY idx_pu_date (winning_date),
  KEY idx_pu_category (category),
  KEY idx_pu_bidder (winning_bidder(191)),
  FULLTEXT KEY ft_pu_name (project_name) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

COLS = (
    "source", "src_id", "project_no", "project_name", "region", "region_raw", "city",
    "category", "purchaser", "agency", "winning_bidder", "winning_amount",
    "winning_date", "budget_amount", "publish_date", "announcement_id",
)


def legacy_rows(src) -> list[tuple]:
    """ztb_clean.bid_project → 宽表行。"""
    src.execute(
        "SELECT id, project_number, project_name, province, city, project_category, "
        "purchaser, agent, successful_bidder, winning_amount, winning_date, "
        "budget_amount, publish_date FROM ztb_clean.bid_project"
    )
    out = []
    for (rid, no, name, prov, city, cat, purchaser, agent, bidder,
         amt, wdate, budget, pub) in src.fetchall():
        out.append((
            "LEGACY", str(rid), no or "", clean_project_name(name), normalize_region(prov, None),
            prov, city, cat, purchaser, agent, bidder, amt, wdate, budget, pub, None,
        ))
    return out


def ccgp_rows(src) -> list[tuple]:
    """bidding.project (+ award 聚合) → 宽表行。"""
    src.execute(
        "SELECT p.project_id, p.project_no, p.project_name, p.region_name, p.purchaser_name, "
        "       p.agency_name, p.award_amount, p.first_publish_time, p.purchaser_address, "
        "       (SELECT GROUP_CONCAT(DISTINCT a.normalized_supplier_name SEPARATOR '、') "
        "          FROM bidding.award a WHERE a.project_no = p.project_no) AS bidders "
        "  FROM bidding.project p"
    )
    out = []
    for (pid, no, name, region, purchaser, agency, amt, pub, addr, bidders) in src.fetchall():
        out.append((
            "CCGP", str(pid), no or "", clean_project_name(name),
            normalize_region(region, addr),
            region, None, None, purchaser, agency, bidders, amt,
            pub.date() if pub else None, None, pub.date() if pub else None, None,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 project_unified 统一宽表")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args(argv)

    import pymysql

    settings = Settings()
    db = settings.mysql_bidding_db
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=db, charset="utf8mb4", autocommit=False,
    )
    cur = conn.cursor()

    if not args.dry_run:
        cur.execute(DDL)
        conn.commit()

    datasets = [("LEGACY", legacy_rows(cur)), ("CCGP", ccgp_rows(cur))]
    for src_name, rows in datasets:
        print(f"{src_name}: {len(rows)} 行")
        if args.dry_run:
            continue
        cur.execute("DELETE FROM project_unified WHERE source=%s", (src_name,))
        ph = ",".join(["%s"] * len(COLS))
        cur.executemany(
            f"INSERT INTO project_unified ({','.join(COLS)}) VALUES ({ph})", rows
        )
        conn.commit()

    if args.dry_run:
        conn.close()
        return 0

    cur.execute(
        "SELECT source, COUNT(*), SUM(region=%s), SUM(winning_amount IS NULL), "
        "       SUM(CHAR_LENGTH(project_name)>150) "
        "FROM project_unified GROUP BY source",
        (UNKNOWN_REGION,),
    )
    print("\n灌数结果:")
    print("  %-8s %7s %9s %9s %9s" % ("源", "行数", "地区未知", "无金额", "名仍超长"))
    for r in cur.fetchall():
        print("  %-8s %7d %9d %9d %9d" % (r[0], r[1], r[2], r[3], r[4]))
    cur.execute("SELECT COUNT(*) FROM project_unified")
    print(f"合计 {cur.fetchone()[0]} 行")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
