"""router 路由评测脚本 — 两种模式。

**模式 1 · 意图集门禁（P4 L3）**：对 `eval/router_intent_set.json` 逐题喂给 router
节点，记录分类结果，输出准确率 + 汇总 json 到 test_report/。用于动态化接线前后对拍：
before/after 两次报告的逐题意图应完全一致，且对 expected 的准确率不降。

**模式 2 · 金标集路由准确率（E5-2）**：对 RAG 金标 jsonl 逐题跑路由，按 `query_type`
算三组口径 —— single_hop 误入 deep_agent <5%（成本闸门）、multi_hop/complex 命中
deep_agent ≥80%（分解收益的前提）。判据见 `agent/registry.py::_seed_deep_agent`。

用法:
    python scripts/run_router_intent_eval.py --out test_report/router_intent_after.json
    python scripts/run_router_intent_eval.py --gold testset_1000_local.jsonl complex_20.jsonl
    python scripts/run_router_intent_eval.py --gold dev_100.jsonl --workers 8 --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import Counter

from langchain_core.messages import HumanMessage

from agent.router import build_router_node
from core.config.settings import Settings
from core.llm.registry import create_llm_for_slot

DATA_DIR = ROOT / "DATA" / "eval_rag"
OUT_DIR = ROOT / "test_report"


def build_registry(*, enable_deep_agent: bool = False):
    """构造路由用注册表（默认与线上一致；--enable-deep-agent 时把预留能力打开）。

    用于「启用 deep_agent 前的误路由检查」：与默认注册表只差 deep_agent 一项。
    """
    from dataclasses import replace

    from agent.registry import (
        CapabilityRegistry,
        _seed_deep_agent,
        _seed_doc_agent,
        _seed_echo_demo,
        seed_builtins,
    )

    reg = CapabilityRegistry()
    seed_builtins(reg)
    reg.register(_seed_echo_demo())
    reg.register(_seed_doc_agent())
    reg.register(replace(_seed_deep_agent(), enabled=enable_deep_agent))
    return reg


def load_set(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("items", data)  # {"items":[...]} 或裸数组


def run(path: Path, out: Path, *, enable_deep_agent: bool = False) -> None:
    items = load_set(path)
    llm = create_llm_for_slot("router", Settings())
    registry = build_registry(enable_deep_agent=enable_deep_agent)
    router_node = build_router_node(llm, registry=registry)
    print(f"候选集: {[c.id for c in registry.enabled_specs()]}")

    rows, correct = [], 0
    for it in items:
        intent = router_node(
            {"messages": [HumanMessage(content=it["q"])]}
        ).get("router_intent", "fallback")
        rows.append({"q": it["q"], "expected": it["expected"], "got": intent})
        correct += intent == it["expected"]

    acc = correct / len(rows)
    dist = Counter(r["got"] for r in rows)
    summary = {
        "total": len(rows),
        "correct": correct,
        "accuracy": round(acc, 4),
        "per_intent_expected": dict(Counter(r["expected"] for r in rows)),
        "per_intent_got": dict(dist),
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"== {path.name} → {out}")
    print(f"   accuracy={acc:.2%} ({correct}/{len(rows)})")
    for r in rows:
        mark = "OK " if r["got"] == r["expected"] else "MISS"
        print(f"   {mark} [{r['expected']:>13}]  {r['got']:<13}  {r['q']}")


def run_gold(paths: list[Path], *, workers: int = 8, enable_deep_agent: bool = True,
             limit: int = 0) -> dict:
    """金标集路由准确率（E5-2）：多数据集合并成一份报告，按 query_type 分组口径。"""
    from evals.datasets import distribution, load_jsonl
    from evals.metrics import build_rows, check_gates, evaluate_routing
    from evals.runners.router import run_router

    samples = []
    for p in paths:
        loaded = load_jsonl(p)
        samples.extend(loaded)
        print(f"金标集: {p.name}  {len(loaded)} 条 {distribution(loaded)}")
    if limit > 0:  # 冒烟：每类各取 limit 条（按 question 排序，可复现）
        picked, seen = [], Counter()
        for s in sorted(samples, key=lambda s: (s.query_type, s.question)):
            if seen[s.query_type] < limit:
                seen[s.query_type] += 1
                picked.append(s)
        samples = picked
        print(f"冒烟截取: {len(samples)} 条 {distribution(samples)}")

    llm = create_llm_for_slot("router", Settings())
    registry = build_registry(enable_deep_agent=enable_deep_agent)
    router_node = build_router_node(llm, registry=registry)
    print(f"候选集: {[c.id for c in registry.enabled_specs()]}")

    def _route(question: str) -> str:
        return router_node({"messages": [HumanMessage(content=question)]}).get(
            "router_intent", "fallback"
        )

    started = time.monotonic()
    got = run_router(
        samples, _route, workers=workers,
        on_progress=lambda done, total: print(f"  ...{done}/{total}"),
    )
    elapsed = time.monotonic() - started

    rows = build_rows(samples, got)
    metrics = evaluate_routing(rows)
    gates = check_gates(metrics)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail_path = OUT_DIR / f"router_gold_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    report = {
        "datasets": [p.name for p in paths],
        "elapsed_seconds": round(elapsed, 1),
        "workers": workers,
        "metrics": metrics,
        "gates": gates,
    }
    report_path = OUT_DIR / f"router_gold_{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n=== 路由准确率（E5-2）===")
    print(f"  样本                  {metrics['samples']}")
    print(f"  route_accuracy        {metrics['route_accuracy']:.4f}")
    print(f"  single_hop 误入率     {metrics['single_hop_misroute_rate']:.4f}  (目标 <0.05)")
    print(f"  multi_hop 命中率      {metrics['multi_hop_hit_rate']:.4f}  (目标 >=0.80)")
    print(f"  complex 命中率        {metrics['complex_hit_rate']:.4f}  (目标 >=0.80)")
    print(f"  实际路由分布          {metrics['got_distribution']}")
    print(f"  混淆 top              {metrics['confusion_top']}")
    print(f"  门禁                  {gates['all_passed']}")
    print(f"  耗时                  {elapsed:.1f}s")
    print(f"\n明细: {detail_path}")
    print(f"报告: {report_path}")
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", type=Path, default=None,
                   help="意图集 JSON（默认 eval/router_intent_set.json）")
    p.add_argument("--out", type=Path, default=ROOT / "test_report" / "router_intent_after.json")
    p.add_argument("--gold", nargs="*", type=Path, default=None,
                   help="金标集 jsonl（相对 DATA/eval_rag，可多个）→ E5-2 路由准确率")
    p.add_argument("--workers", type=int, default=8, help="金标模式并发度")
    p.add_argument("--limit", type=int, default=0,
                   help="金标模式冒烟：每类各取 N 条（0=全部）")
    p.add_argument(
        "--enable-deep-agent", action="store_true",
        help="意图集模式：把预留的 deep_agent 打开后再跑（误路由检查用）",
    )
    args = p.parse_args()

    if args.gold:
        paths = [g if g.is_absolute() else DATA_DIR / g for g in args.gold]
        run_gold(paths, workers=args.workers, limit=args.limit)
        return

    run(
        args.set or ROOT / "eval" / "router_intent_set.json",
        args.out,
        enable_deep_agent=args.enable_deep_agent,
    )


if __name__ == "__main__":
    main()
