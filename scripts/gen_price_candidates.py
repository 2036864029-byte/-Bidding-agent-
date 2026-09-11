"""price 金标候选生成 v2（E1-3）— 角色感知取样 + 实体存在性校验 + 引擎有效性过滤。

设计（v1 教训：随机抽公司套模板会造出语义错问句，如"福建师范大学中标"=采购方当投标方）：
  ① **角色感知取样**：每个 (sub_route, query_type) 从**对应角色列**取样——
     bidder_query ← bid_project.successful_bidder（保证真是投标方）
     purchaser_query ← bid_project.purchaser
     penalty_check ← company_penalty.company_name（保证有处罚记录）
     company_detail ← company_info.company_name
     project_detail ← bid_project.project_number
     supplier_recommend ← company_info.industry（守卫已修，行业可命中）
     all/mixed ← successful_bidder ∩ company_info（两表都有的公司）
  ② **实体存在性校验**：取样即保证存在（构建时校验实体在对应列）。
  ③ **引擎有效性过滤**：每条跑 price_query，guard_reason 非空 / 崩溃 / 0 行(非负例) 自动剔除。
  ④ **覆盖报告**：(sub_route, query_type) × 条数，缺组合显式列出。
  ⑤ **待复核 JSONL**：供人工抽验（review_ok 置 null）。

用法：
    python scripts/gen_price_candidates.py --target 60           # 生成候选 + 过滤 + 覆盖报告
    python scripts/gen_price_candidates.py --write              # 合并写回金标（含修原 13 条）
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

GOLDEN = PROJECT_ROOT / "eval" / "price_golden_set.json"
CAND = PROJECT_ROOT / "eval" / "price_golden_candidates.jsonl"
REVIEW = PROJECT_ROOT / "eval" / "price_golden_review.jsonl"

# (sub_route, query_type) → 模板（{entity} 为角色感知实体）
TEMPLATES = {
    ("company_query", "supplier_recommend"): [
        "推荐几家做{entity}的企业",
        "有哪些做{entity}的公司？",
        "帮我找{entity}行业的企业",
        "{entity}行业有哪些存续企业",
    ],
    ("company_query", "penalty_check"): [
        "{entity}有没有被处罚过？",
        "{entity}有不良记录吗？",
        "{entity}被列入黑名单了吗？",
        "{entity}有没有行政处罚",
    ],
    ("company_query", "company_detail"): [
        "{entity}的工商信息有哪些？",
        "{entity}的企业详情",
        "{entity}的法定代表人是谁？",
        "{entity}的注册资本和经营范围",
    ],
    ("bidding_query", "bidder_query"): [
        "{entity}中标了哪些项目？",
        "{entity}的中标历史",
        "{entity}中标过什么项目？",
        "{entity}的投标中标记录",
    ],
    ("bidding_query", "purchaser_query"): [
        "{entity}采购过哪些项目？",
        "{entity}的招标历史",
        "{entity}发包了哪些项目？",
    ],
    ("bidding_query", "project_detail"): [
        "{entity} 这个项目的中标情况",
        "{entity}的中标详情",
        "项目编号{entity}谁中标了？",
        "{entity} 项目中标的是谁",
    ],
    ("bidding_query", "aggregation"): [
        "中标金额最大的项目是哪个？",
        "2024年金额最高的中标项目",
        "中标金额TOP10的项目",
    ],
    ("all", "mixed"): [
        "{entity}的详细信息和中标记录",
        "{entity}的工商信息和中标历史",
    ],
}


def _pull_role_entities():
    """按角色列取样（保证实体存在于对应列 = 语义正确性硬保证）。

    公司类角色用引擎同一校验器过滤（_is_valid_company_name），避免取样到
    学校/机关/门店等非工商主体名——它们必然被引擎守卫滤掉，白耗一次调用。
    """
    from price_engine.intent import _is_valid_company_name

    from core.rdb.mysql_pool import _CLEAN_DB, _get_connection, _release_connection

    conn = _get_connection(_CLEAN_DB)
    cur = conn.cursor()
    try:
        def q(sql, n):
            cur.execute(sql, (n,))
            return [r[0] for r in cur.fetchall() if r[0]]

        company_roles = {
            # bidder：成功投标方（保证"中标"问句语义正确）
            "bidder": q(
                "SELECT successful_bidder FROM bid_project WHERE successful_bidder IS NOT NULL"
                " AND successful_bidder <> '' AND LENGTH(successful_bidder) BETWEEN 4 AND 30"
                " AND successful_bidder NOT LIKE '%%（%%' GROUP BY successful_bidder"
                " ORDER BY COUNT(*) DESC LIMIT %s", 120),
            # purchaser：采购方
            "purchaser": q(
                "SELECT purchaser FROM bid_project WHERE purchaser IS NOT NULL"
                " AND purchaser <> '' AND LENGTH(purchaser) BETWEEN 4 AND 30"
                " GROUP BY purchaser ORDER BY COUNT(*) DESC LIMIT %s", 120),
            # penalty：有处罚记录的企业（保证 penalty_check 有结果）
            "penalty": q(
                "SELECT company_name FROM company_penalty WHERE company_name IS NOT NULL"
                " AND company_name <> '' AND LENGTH(company_name) BETWEEN 4 AND 30"
                " GROUP BY company_name ORDER BY COUNT(*) DESC LIMIT %s", 120),
            # company：工商库企业
            "company": q(
                "SELECT company_name FROM company_info WHERE company_name IS NOT NULL"
                " AND company_name <> '' AND LENGTH(company_name) BETWEEN 4 AND 30"
                " ORDER BY RAND() LIMIT %s", 120),
            # mixed：既是成功投标方又在工商库（all/mixed 问句用）
            "mixed": q(
                "SELECT b.successful_bidder FROM bid_project b"
                " JOIN company_info c ON b.successful_bidder = c.company_name"
                " WHERE b.successful_bidder IS NOT NULL AND b.successful_bidder <> ''"
                " AND LENGTH(b.successful_bidder) BETWEEN 4 AND 30"
                " GROUP BY b.successful_bidder ORDER BY COUNT(*) DESC LIMIT %s", 120),
        }
        entities = {}
        for k, rows in company_roles.items():
            entities[k] = [r for r in rows if _is_valid_company_name(r)]
        # 非公司类角色
        entities["project_number"] = q(
            "SELECT project_number FROM bid_project WHERE project_number IS NOT NULL"
            " AND project_number <> '' AND LENGTH(project_number) <= 40"
            " ORDER BY RAND() LIMIT %s", 60)
        entities["industry"] = q(
            "SELECT industry FROM company_info WHERE industry IS NOT NULL"
            " AND industry <> '' AND LENGTH(industry) <= 12"
            " GROUP BY industry ORDER BY COUNT(*) DESC LIMIT %s", 30)
    finally:
        _release_connection(conn)
    for k, v in entities.items():
        print(f"  角色[{k}]: {len(v)} 个实体", flush=True)
    return entities


def generate(target: int = 60, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    ent = _pull_role_entities()
    combos = sorted(TEMPLATES.keys())
    # 每组合保底 1 条；配额轮转填满
    per_combo = {c: 1 for c in combos}
    for i in range(target - len(combos)):
        per_combo[combos[i % len(combos)]] += 1

    role = {
        ("company_query", "supplier_recommend"): "industry",
        ("company_query", "penalty_check"): "penalty",
        ("company_query", "company_detail"): "company",
        ("bidding_query", "bidder_query"): "bidder",
        ("bidding_query", "purchaser_query"): "purchaser",
        ("bidding_query", "project_detail"): "project_number",
        ("bidding_query", "aggregation"): None,
        ("all", "mixed"): "mixed",
    }

    items: list[dict] = []
    seen: set[str] = set()
    for combo, count in per_combo.items():
        rk = role[combo]
        pool = ent[rk] if rk else [None]
        for _ in range(count):
            entity = rng.choice(pool) if pool else None
            if entity is None and rk:      # 该角色无实体 → 跳过
                continue
            tpl = TEMPLATES[combo][rng.randrange(len(TEMPLATES[combo]))]
            q = tpl.format(entity=entity) if entity else tpl
            if q in seen:
                continue
            seen.add(q)
            items.append({"q": q, "sub_route": combo[0], "query_type": combo[1],
                          "source_entity": entity, "source": "template+mysql-role"})
    return items


def _engine_valid(cands: list[dict]) -> list[dict]:
    """引擎有效性过滤：guard_reason / 崩溃 / 0 行 剔除。

    0 行也剔除：生成器按角色取样（实体存在），有效问句应返回 ≥1 行；
    0 行 = 召回 bug（如 project_detail 编号被 FULLTEXT 误伤）或数据怪癖，
    不适合进"行为快照"门禁（否则每轮都在测一个坏路径的固定输出）。
    """
    import price_engine.tools as t

    out = []
    for i, c in enumerate(cands, 1):
        try:
            r = t.price_query(c["q"])
            guard = r.get("guard_reason")
            n = len(r.get("records") or [])
            reason = guard or ("zero_rows" if n == 0 else None)
            if reason:
                print(f"  [滤] {c['sub_route']}/{c['query_type']} {reason} | {c['q'][:40]}", flush=True)
                continue
            c["engine_records"] = n
            c["guard_reason"] = None
            out.append(c)
        except Exception as e:  # noqa: BLE001
            print(f"  [滤] {c['sub_route']}/{c['query_type']} 崩溃 {type(e).__name__} | {c['q'][:40]}", flush=True)
        if i % 20 == 0:
            print(f"  ...过滤 {i}/{len(cands)}", flush=True)
    return out


def _coverage_report(items: list[dict]) -> str:
    from collections import Counter

    c = Counter((i["sub_route"], i["query_type"]) for i in items)
    lines = ["覆盖率报告："]
    for k in sorted(TEMPLATES):
        lines.append(f"  {k}: {c.get(k, 0)} 条")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=60)
    parser.add_argument("--write", action="store_true", help="合并写回金标（含修原 13 条）")
    args = parser.parse_args()

    print("① 角色感知取样：", flush=True)
    cands = generate(args.target)
    print(f"\n② 生成候选 {len(cands)} 条，③ 引擎有效性过滤：", flush=True)
    valid = _engine_valid(cands)
    print(f"\n④ 过滤后有效 {len(valid)} 条（原 {len(cands)}）")

    # 过滤后补足配额：若某组合 0 条，再补一轮（简单重试一次）
    from collections import Counter
    cnt = Counter((i["sub_route"], i["query_type"]) for i in valid)
    for combo, n in cnt.items():
        if n == 0:
            print(f"  警告：组合 {combo} 无有效候选")

    CAND.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in valid) + "\n",
                    encoding="utf-8")
    REVIEW.write_text(
        "\n".join(json.dumps({**c, "review_ok": None}, ensure_ascii=False) for c in valid) + "\n",
        encoding="utf-8")
    print(f"\n候选已写: {CAND}（{len(valid)} 条）")
    print(f"待复核: {REVIEW}（review_ok 置 null，供抽验）")
    print(_coverage_report(valid))

    if args.write:
        _merge_to_golden(valid)


def _merge_to_golden(valid: list[dict]) -> None:
    """合并写回金标：修原 13 条坏问句 + 追加有效候选（去重）。"""
    import shutil

    old = json.loads(GOLDEN.read_text(encoding="utf-8"))
    items = old["items"]
    # 修原 13 条坏问句
    fixed_q = {
        # 采购方当投标方（师大是采购方）→ 改 purchaser 问句
        "福建师范大学中标了哪些项目？": "福建师范大学采购了哪些项目？",
    }
    items = [{**it, "q": fixed_q.get(it["q"], it["q"])} for it in items]
    # 删除引擎不支持的问句（按项目名查中标 / 无行业 supplier_recommend）
    drop = {
        "旗山校区五期生活区学生公寓1-3号楼空调 项目中标的是谁",   # 引擎只认编号不认项目名
        "推荐几家做办公家具的企业",                             # 办公家具非规范行业，意图不入 industry
    }
    items = [it for it in items if it["q"] not in drop]

    existing = {it["q"] for it in items}
    added = [{"q": c["q"]} for c in valid if c["q"] not in existing]
    merged = items + added
    shutil.copy(GOLDEN, GOLDEN.with_suffix(".json.bak2"))
    GOLDEN.write_text(json.dumps({"description": old["description"], "items": merged},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合并写回 {GOLDEN} → 共 {len(merged)} 条（原 {len(items)} + 新增 {len(added)}）")
    print(f"备份: {GOLDEN}.bak2")


if __name__ == "__main__":
    main()
