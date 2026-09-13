# 功能：闸门判据扫描（诊断）— 不同判据下的误入率 / 漏答率。
"""诊断脚本：为「deep_agent 场景化启用」的判定闸门选判据（不参与生产）。

**背景**：闸门的代价是硬的（deep_agent 实测 15.8s/题 vs 单节点 doc_qa 0.5s），
判太宽会让简单文件问题掉进 agent。故先用已标注样本扫一遍各判据的
**误入率**（不该进 agent 却进了）与 **漏答率**（该进却没进）。

**标签从哪来**：`eval/file_golden_set.json` 的 query_type —— 它标注的是
「这个问题需要几个源」，等价于「单节点能不能答全」：
  - 需要 agent（跨源）：file_kb / file_price / file_multi
  - 不需要（单源可解）：file_only / no_file_needed / multi_file
⚠️ **这是我的判断，不是用户业务口径** —— 阈值最终要用户拍板。

**无文件对照**：`complex_20.jsonl`（20 条长复合问题，expected_route=deep_agent，
但**不带文件**）。用来看「带文件」这个前置条件会把什么排除在外。

用法：
    python scripts/diag_gate_criteria.py
    python scripts/diag_gate_criteria.py --show-items     # 附带逐条明细
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FILE_GOLDEN = PROJECT_ROOT / "eval" / "file_golden_set.json"
COMPLEX = PROJECT_ROOT / "DATA" / "eval_rag" / "complex_20.jsonl"

#: 需要 agent（跨源）的 query_type；其余为单源可解
NEEDS_AGENT = {"file_kb", "file_price", "file_multi"}

#: 跨源信号关键词（复用 routing_rules.yaml 的 handoff 词表 + 业务数据词）
KW_COMPLIANCE = re.compile(
    r"合规|符合|依据|违反|是否合法|合法吗|规定|法规|条例|法条|第几条|法律|上限"
)
KW_DATA = re.compile(r"处罚|不良记录|失信|中标记录|采购记录|工商|企业信息|过往|有没有中标")

#: 调优版（诊断用）：按 ⑫ 的 4 处错误逐条补词。
#: ⚠️ **这是在**同一批 49 条样本上调出来的 → 过拟合**，只能当"关键词路线的上界"看，
#: 不能当真实表现。真实数字必须拿**用户真实提问**（sample_traces_for_golden 采样）来测。
KW_COMPLIANCE_TUNED = re.compile(
    r"合规|符合|依据|违反|是否合法|合法吗|规定|法规|条例|法条|第几条|法律"
    r"|构成|限制供应商|差别待遇|不合理|资格条件"      # 补：⑫ 漏答的「不合理条件限制供应商」
)   # 去掉过泛的「上限」——它把纯法规问「法定上限是多少」误拉进 agent
KW_DATA_TUNED = re.compile(
    r"处罚|不良\w{0,4}记录|失信|中标价|中标金额|中标记录|采购记录|工商|企业信息|过往|有没有中标"
)   # 补：中标价/中标金额；放宽「不良记录」以匹配「不良行为记录」

QMARK = re.compile(r"[？?]")

#: 「问题指向**本文档**」的信号。这是把「需要文件 + 别的源」与「只需要别的源」
#: 分开的关键——两者的话题关键词完全相同（如「中标金额」），
#: 区别只在于问的是"**这份文件里**怎么写的"还是"行业上一般如何"。
#: 实测（⑯ 的最后一处误入）："近期中标金额超过一千万元的…项目有哪些？" 被
#: 「中标金额」拉进 agent，但它其实与在场文件无关，单节点 price_inquiry 就能答。
DOC_REF = re.compile(
    r"这份|本标书|本文件|该文件|文件里|文件中|上传的|这几份|这两份|标书里|招标文件里|这份文件"
)


def _clauses(q: str) -> list[str]:
    """与 Layer 1 同一口径的子句拆分（public_kb.tools._split_clauses）。"""
    from public_kb.tools import _split_clauses

    return _split_clauses(q, 8)


def criteria():
    """→ [(名称, 判定函数, 说明)]。判定函数：(问题, 子句数) -> bool（是否送 agent）。"""
    return [
        ("① 仅「带文件」(基线)", lambda q, n: True, "有文件就进 —— 误入率上限"),
        ("② len>50", lambda q, n: len(q) > 50, "按问题长度"),
        ("③ len>80", lambda q, n: len(q) > 80, ""),
        ("④ len>100", lambda q, n: len(q) > 100, ""),
        ("⑤ len>150", lambda q, n: len(q) > 150, ""),
        ("⑥ len>200", lambda q, n: len(q) > 200, ""),
        ("⑦ 子句≥2", lambda q, n: n >= 2, "按子句数（Layer 1 同口径）"),
        ("⑧ 子句≥3", lambda q, n: n >= 3, ""),
        ("⑨ 问号≥2", lambda q, n: len(QMARK.findall(q)) >= 2, "多子问的浅层信号"),
        ("⑩ 合规类关键词", lambda q, n: bool(KW_COMPLIANCE.search(q)), "需查法规"),
        ("⑪ 业务数据关键词", lambda q, n: bool(KW_DATA.search(q)), "需查结构化库"),
        ("⑫ ⑩或⑪（跨源探测）", lambda q, n: bool(KW_COMPLIANCE.search(q) or KW_DATA.search(q)), ""),
        ("⑬ len>80 或 子句≥2", lambda q, n: len(q) > 80 or n >= 2, ""),
        ("⑭ 子句≥2 或 ⑫", lambda q, n: n >= 2 or bool(KW_COMPLIANCE.search(q) or KW_DATA.search(q)), ""),
        ("⑮ len>200 或 子句≥2 或 ⑫", lambda q, n: len(q) > 200 or n >= 2
         or bool(KW_COMPLIANCE.search(q) or KW_DATA.search(q)), "计划原文的三条并列"),
        ("⑯ 调优关键词（⚠️过拟合）", lambda q, n: bool(
            KW_COMPLIANCE_TUNED.search(q) or KW_DATA_TUNED.search(q)),
         "只在同一批样本上调的，仅作上界参考"),
        ("⑰ 指向本文档 + 跨源词", lambda q, n: bool(
            DOC_REF.search(q) and (KW_COMPLIANCE.search(q) or KW_DATA.search(q))),
         "结构性判据：非关键词调优"),
        ("⑱ 指向本文档 + 调优词", lambda q, n: bool(
            DOC_REF.search(q) and (KW_COMPLIANCE_TUNED.search(q) or KW_DATA_TUNED.search(q))),
         "⑰ 的调优版"),
    ]


def _load_labelled() -> list[dict]:
    data = json.loads(FILE_GOLDEN.read_text(encoding="utf-8"))
    out = []
    for it in data["items"]:
        q = it["q"]
        out.append({
            "q": q, "qt": it["query_type"], "n_clauses": len(_clauses(q)),
            "needs": it["query_type"] in NEEDS_AGENT,
        })
    return out


def _load_complex() -> list[dict]:
    if not COMPLEX.exists():
        return []
    rows = []
    for line in COMPLEX.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        it = json.loads(line)
        q = it["question"]
        rows.append({"q": q, "n_clauses": len(_clauses(q)), "ref": it.get("expected_route")})
    return rows


def _rate(hit: int, total: int) -> str:
    return "—" if not total else f"{hit / total:5.1%}"


def main() -> None:
    ap = argparse.ArgumentParser(description="闸门判据扫描（诊断）")
    ap.add_argument("--show-items", action="store_true", help="附带逐条明细")
    args = ap.parse_args()

    items = _load_labelled()
    pos = [x for x in items if x["needs"]]
    neg = [x for x in items if not x["needs"]]
    print(f"标注样本 {len(items)} 条：需要 agent（跨源）{len(pos)} · 单源可解 {len(neg)}")
    print(f"  跨源构成：{ {k: sum(1 for x in pos if x['qt'] == k) for k in sorted(NEEDS_AGENT)} }")
    print(f"  单源构成：{ {k: sum(1 for x in neg if x['qt'] == k) for k in sorted({x['qt'] for x in neg})} }")
    print("\n判据扫描（前提：会话里**有文件**）")
    print(f"{'判据':<24}{'送 agent':>8}{'误入率':>9}{'漏答率':>9}{'命中率':>9}")
    print("-" * 60)
    rows = []
    for name, fn, _note in criteria():
        mis = sum(1 for x in neg if fn(x["q"], x["n_clauses"]))
        miss = sum(1 for x in pos if not fn(x["q"], x["n_clauses"]))
        sent = sum(1 for x in items if fn(x["q"], x["n_clauses"]))
        rows.append((name, sent, mis, miss))
        print(f"{name:<24}{sent:>8}{_rate(mis, len(neg)):>9}"
              f"{_rate(miss, len(pos)):>9}{_rate(len(pos) - miss, len(pos)):>9}")

    if args.show_items:
        print("\n逐条（判据⑫ 跨源探测 = 合规类 or 业务数据类）:")
        fn2 = dict((n, f) for n, f, _ in criteria())["⑫ ⑩或⑪（跨源探测）"]
        for x in items:
            pred = fn2(x["q"], x["n_clauses"])
            verdict = ("✅" if pred == x["needs"] else
                       ("❌误入" if pred else "❌漏答"))
            print(f"  {verdict} [{x['qt']:14s}|子句{x['n_clauses']}|{len(x['q']):3d}字] {x['q'][:42]}")

    cx = _load_complex()
    if cx:
        lens = [len(x["q"]) for x in cx]
        print(f"\n无文件对照：complex_20（{len(cx)} 条长复合问，长度 {min(lens)}~{max(lens)} 字，"
              f"自带 expected_route=deep_agent）")
        print("  ⚠️ 这批**都没有文件** → 闸门若含「带文件」前置，它们 **0/20 进 agent**，")
        print("     自带的 deep_agent 标签随之作废。下面的比例是**丢掉该前置、纯按文本判据**")
        print("     的结果（即 E5 实测天花板 ~73% 的那条路）——供你判断要不要走这条路：")
        for name, fn, _ in criteria():
            hit = sum(1 for x in cx if fn(x["q"], x["n_clauses"]))
            print(f"    {name:<24} {hit:>3}/{len(cx)} = {hit / len(cx):5.1%}")
        print("  ⚠️ 注意：上表用的样本整体**偏长**（359~706 字），所以长度类判据在这里")
        print("     看起来都能全中 —— 这是样本偏差，不代表它在真实分布上有效")
        print("     （49 条标注样本里长度判据漏答 78~87%）。")


if __name__ == "__main__":
    main()
