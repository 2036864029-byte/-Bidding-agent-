"""建 `bidding` 库 + 应用交付包里的 `mysql_schema.sql`（11 张表 + 外键）。

为什么要脚本而不是手敲 mysql 客户端：`mysql_schema.sql` **不含 `CREATE DATABASE`**，
直接 `SOURCE` 会落到当前库；且建库语句需要 utf8mb4 与排序规则显式指定。
本脚本把"建库 → 选库 → 按序建表"固化成一步，可重复执行（幂等：DDL 全是 IF NOT EXISTS）。

用法::

    python scripts/init_bidding_db.py --dry-run      # 只打印将执行的语句
    python scripts/init_bidding_db.py                # 建库建表
    python scripts/init_bidding_db.py --db bidding2  # 换个库名
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import Settings

SCHEMA = Path(__file__).resolve().parents[1] / "delivery_package" / "mysql_schema.sql"


def split_statements(sql: str) -> list[str]:
    """按 `;` 切语句。先剔除纯注释行——注释里可能带分号，会切歪。"""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def connect(settings: Settings, database: str | None = None):
    import pymysql

    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="建 bidding 库并应用交付包 schema")
    ap.add_argument("--db", default=None, help="库名（默认取 settings.mysql_bidding_db）")
    ap.add_argument("--schema", default=str(SCHEMA), help="DDL 文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args(argv)

    settings = Settings()
    db = args.db or settings.mysql_bidding_db
    ddl = split_statements(Path(args.schema).read_text(encoding="utf-8"))

    print(f"库: {db}   DDL 语句数: {len(ddl)}")
    if args.dry_run:
        for s in ddl:
            print("  " + s.split("\n")[0][:100])
        return 0

    conn = connect(settings)
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db}` "
            "DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        # 建表语句必须按文件顺序执行：project → announcement → 其余（外键依赖）
        for s in ddl:
            cur.execute(f"USE `{db}`")
            cur.execute(s)
    conn.close()

    conn = connect(settings, db)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s ORDER BY table_name",
            (db,),
        )
        tables = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.table_constraints "
            "WHERE table_schema=%s AND constraint_type='FOREIGN KEY'",
            (db,),
        )
        fks = cur.fetchone()[0]
    conn.close()

    print(f"建表完成: {len(tables)} 张, 外键 {fks} 条")
    for t in tables:
        print(f"  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
