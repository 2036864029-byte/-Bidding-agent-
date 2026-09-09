"""router 意图集验证脚本（P4 L3 门禁：router 意图集不降）。

对 eval/router_intent_set.jsonl 逐题喂给**router 节点**（不经业务节点），记录
分类结果，输出与 expected 的准确率 + 汇总 json 到 test_report/。用于 P4 动态化
接线前后对拍：before/after 两次报告的逐题意图应完全一致（字节级 prompt/tool
等价 → 分类不变），且对 expected 的准确率不降。

用法:
    python scripts/run_router_intent_eval.py --out test_report/router_intent_after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import Counter

from langchain_core.messages import HumanMessage

from agent.router import build_router_node
from core.config.settings import Settings
from core.llm.registry import create_llm_for_slot


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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", type=Path, default=ROOT / "eval" / "router_intent_set.json")
    p.add_argument("--out", type=Path, default=ROOT / "test_report" / "router_intent_after.json")
    p.add_argument(
        "--enable-deep-agent", action="store_true",
        help="把预留的 deep_agent 打开后再跑（误路由检查用）",
    )
    args = p.parse_args()
    run(args.set, args.out, enable_deep_agent=args.enable_deep_agent)


if __name__ == "__main__":
    main()
