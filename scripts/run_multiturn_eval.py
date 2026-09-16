# 功能：多轮对话评测入口（Phase 0/1 落地，2026-09-16）。
"""多轮对话评测（offline 门禁 / graph 在线）。

用法：
    # offline（默认）：只跑确定性改写预判，零 LLM/零网络，可进门禁
    python scripts/run_multiturn_eval.py --mode offline --gate

    # graph：跑完整 AgentGraph（真实 LLM + Milvus），在线看 intent/branch/拒答
    python scripts/run_multiturn_eval.py --mode graph --workers 2

产物写 test_report/multiturn/（per_turn|per_sample.jsonl + summary.json + report.md）。
offline 的 summary 直接喂 `evals.gates.check_multiturn` 判门禁。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DATA = ROOT / "DATA" / "eval_multiturn" / "multiturn_30.jsonl"
DEFAULT_OUT = ROOT / "test_report" / "multiturn"


def main() -> int:
    # Windows 控制台默认 GBK 编码，✅/❌ 等 emoji 无法编码 → 显式切 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001  非 TTY/旧版本忽略
            pass

    parser = argparse.ArgumentParser(description="多轮对话评测")
    parser.add_argument("--mode", choices=["offline", "graph"], default="offline")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="金标 JSONL")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="产物目录")
    parser.add_argument("--workers", type=int, default=1, help="graph 模式样本并发")
    parser.add_argument("--gate", action="store_true", help="offline 模式判门禁并打印")
    args = parser.parse_args()

    from evals.datasets.multiturn import categories, load_jsonl
    from evals.gates import all_passed, check_multiturn, render_gates

    samples = load_jsonl(args.data)
    print(f"加载金标 {len(samples)} 组 · 分布 {categories(samples)}")

    if args.mode == "offline":
        from evals.runners.multiturn import run_precheck

        res = run_precheck(samples)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "per_turn.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in res["per_turn"]) + "\n",
            encoding="utf-8",
        )
        (out / "summary.json").write_text(
            json.dumps(res["summary"], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        s = res["summary"]
        print(f"offline: {s['passed_turns']}/{s['n_turns']} 轮通过 · "
              f"省略/指代召回 {s['ellipsis_recall']} · "
              f"自包含误改写 {s['self_contained_false_positive']}")
        if args.gate:
            print()
            print(render_gates(check_multiturn(s), "多轮改写预判门禁"))
            return 0 if all_passed(check_multiturn(s)) else 1
        return 0

    # graph 在线
    from agent import AgentGraph
    from evals.runners.multiturn import run_multiturn

    agent = AgentGraph(checkpointer_backend="memory")
    res = run_multiturn(samples, agent, workers=args.workers, out_dir=args.out)
    s = res["summary"]
    print(f"graph: 样本 {s['n_samples']} · 轮次 {s['n_turns']} · "
          f"追问轮 {s['followup_turns']} · 拒答率 {s['refusal_rate']} · "
          f"错误 {s['errors']} · 耗时 {s['elapsed_s']}s")
    print(f"产物: {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
