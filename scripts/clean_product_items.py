"""清洗产品明细 → `bidding.project_product_clean`（一行一个产品）。

## 为什么需要重建而不是 UPDATE

表的 `product_name` 列只认 `货物名称` / `服务名称` 两个键，但 `raw_row` 里实际有
**三套公告模板**（实测 1,489 行 100% 覆盖，无遗漏）：

| 模板 | 产品名键 | 配套键 | 行数 |
|---|---|---|---|
| 货物表 | `货物名称` | 货物品牌 / 货物型号 / 货物数量 / 货物单价(元) | 518 |
| 服务表 | `服务名称` | 服务时间 / 服务标准 / 服务范围 | 348 |
| 标的表 | `标的名称` | 品牌 / 规格型号 / 数量 / 单价(元) / 投标报价（元） | 623 |

所以 623 行（41.8%）在原列里"没有名字"——其实名字在 `标的名称` 里。
另有 105 行原列的 `product_name` 是**表头串**（`货物名称`/`详见附件`/`服务名称`），
那是解析时抓错了单元格。

## 顺带解决的两件事

1. **一格多产品**：`超离心研磨仪;刀式研磨仪配置1;…`（170 字含 20 个产品）
   按 `[、;；]` 拆开；有 51 行连单价也是并列的（`601990.00、326869.00、…`），
   数量对齐时按下标配对。
2. **品牌/型号/数量/单价**从原文列结构化取出，不再是一串拼接字符串。

## 关于"产品 Top 榜"的实话

清洗后不同产品数仍然远大于行数（长尾极端），**排名本身意义有限**——
本脚本的产出是**正确的产品行**，榜单能否用要看清洗后的频次分布（见脚本输出）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings

# 占位/表头串：不是产品，丢弃（原列里 105 行是这些）
PLACEHOLDERS = {
    "货物名称", "服务名称", "标的名称", "标项名称", "项目名称", "品名", "品目名称",
    "详见附件", "见附件", "详见招标文件", "详见开标一览表", "详见分项报价表",
    "详见分项报价明细表", "详见分项报价明细表。", "详见投标文件", "详见投标分项报价表",
    "详见8.2主要标的信息", "详见", "无", "／", "/", "-", "—", "略",
}
PLACEHOLDER_RE = re.compile(r"^(详见|见)\S*$")

# 三套模板的键（按优先级）
NAME_KEYS = ("标的名称", "货物名称", "服务名称")
BRAND_KEYS = ("品牌", "货物品牌")
MODEL_KEYS = ("规格型号", "规格型号（或项目特征描述）", "货物型号")
QTY_KEYS = ("数量/单位", "数量", "货物数量")
PRICE_KEYS = ("单价(元)", "货物单价(元)", "投标报价（元）", "单价")
SUPPLIER_KEYS = ("供应商名称", "中标供应商", "成交供应商")

SPLIT_RE = re.compile(r"[、;；]")


def pick(d: dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def is_placeholder(name: str) -> bool:
    n = name.strip().strip("。.;；、,， ")
    return (not n) or n in PLACEHOLDERS or bool(PLACEHOLDER_RE.match(n))


def split_aligned(name: str, price: str) -> list[tuple[str, str]]:
    """按 [、;；] 拆名；单价并列且数量对齐时按下标配对，否则单价留空。"""
    names = [x.strip(" 。.;；、,，") for x in SPLIT_RE.split(name)]
    names = [x for x in names if x and not is_placeholder(x)]
    if not names:
        return []
    if len(names) == 1:
        return [(names[0], price)]
    ps = [x.strip() for x in SPLIT_RE.split(price)] if price else []
    if len(ps) == len(names):
        return list(zip(names, ps))
    return [(n, "") for n in names]


DDL = """
CREATE TABLE IF NOT EXISTS project_product_clean (
  clean_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  announcement_id VARCHAR(64)  NOT NULL,
  project_no      VARCHAR(128) DEFAULT NULL,
  supplier_name   VARCHAR(255) DEFAULT NULL,
  product_name    VARCHAR(512) NOT NULL COMMENT '清洗后单个产品名',
  product_name_raw VARCHAR(1024) DEFAULT NULL COMMENT '原单元格原文',
  name_key        VARCHAR(16)  DEFAULT NULL COMMENT '命中哪套模板',
  brand           VARCHAR(255) DEFAULT NULL,
  model           VARCHAR(1024) DEFAULT NULL COMMENT '规格型号；实测最长 878（含长描述）',
  quantity        VARCHAR(128) DEFAULT NULL,
  unit_price      VARCHAR(255) DEFAULT NULL COMMENT '实测最长 153（多产品并列单价串）',
  PRIMARY KEY (clean_id),
  KEY idx_ppc_name (product_name(191)),
  KEY idx_ppc_ann (announcement_id),
  KEY idx_ppc_project (project_no),
  KEY idx_ppc_supplier (supplier_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

COLS = ("announcement_id", "project_no", "supplier_name", "product_name",
        "product_name_raw", "name_key", "brand", "model", "quantity", "unit_price")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清洗产品明细到 project_product_clean")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import pymysql

    s = Settings()
    conn = pymysql.connect(host=s.mysql_host, port=s.mysql_port, user=s.mysql_user,
                           password=s.mysql_password, database=s.mysql_bidding_db,
                           charset="utf8mb4", autocommit=False)
    cur = conn.cursor()

    if not args.dry_run:
        cur.execute(DDL)
        conn.commit()

    cur.execute("SELECT announcement_id, project_no, supplier_name, raw_row "
                "FROM project_product_item")
    src = cur.fetchall()

    out_rows: list[tuple] = []
    stats = {"no_raw": 0, "no_name": 0, "placeholder": 0, "multi": 0, "rows": 0}
    key_hits: dict[str, int] = {}

    for ann, pno, supplier, raw in src:
        if not raw:
            stats["no_raw"] += 1
            continue
        d = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(d, dict):
            stats["no_raw"] += 1
            continue

        name_key = ""
        name = ""
        for k in NAME_KEYS:
            if isinstance(d.get(k), str) and d[k].strip():
                name_key, name = k, d[k].strip()
                break
        if not name or is_placeholder(name):
            stats["no_name" if not name else "placeholder"] += 1
            continue
        key_hits[name_key] = key_hits.get(name_key, 0) + 1

        price = pick(d, PRICE_KEYS)
        parts = split_aligned(name, price)
        if len(parts) > 1:
            stats["multi"] += 1

        for pname, pprice in parts:
            out_rows.append((
                ann, pno, supplier or pick(d, SUPPLIER_KEYS), pname, name,
                name_key, pick(d, BRAND_KEYS), pick(d, MODEL_KEYS),
                pick(d, QTY_KEYS), pprice,
            ))
            stats["rows"] += 1

    print(f"源行 {len(src)} → 产品行 {stats['rows']}")
    print(f"  丢弃: 无 raw_row {stats['no_raw']}, 无名字 {stats['no_name']}, 占位串 {stats['placeholder']}")
    print(f"  多产品单元格拆开 {stats['multi']} 行")
    print(f"  模板命中: {key_hits}")

    if args.dry_run:
        conn.close()
        return 0

    cur.execute("DELETE FROM project_product_clean")
    ph = ",".join(["%s"] * len(COLS))
    for i in range(0, len(out_rows), 500):
        cur.executemany(
            f"INSERT INTO project_product_clean ({','.join(COLS)}) VALUES ({ph})",
            out_rows[i:i + 500],
        )
    conn.commit()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT product_name) FROM project_product_clean")
    tot, dis = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM (SELECT product_name FROM project_product_clean "
                "GROUP BY product_name HAVING COUNT(*)>=3) t")
    ge3 = cur.fetchone()[0]
    cur.execute("SELECT product_name, COUNT(*) c FROM project_product_clean "
                "GROUP BY product_name ORDER BY c DESC LIMIT 10")
    print(f"\n入表 {tot} 行, 不同产品 {dis}; 出现>=3次的产品 {ge3} 个")
    print("Top10:")
    for n, c in cur.fetchall():
        print(f"  {c:4d}  {n[:44]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
