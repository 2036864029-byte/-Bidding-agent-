"""Agent 轨迹评测入口（薄 CLI）— 逻辑在 `evals/runners/agent_trace`。

对样本逐条跑 deep_agent，按 `data.trace` 出**客观轨迹指标**（零人工）+ 可选的
工具选择/工具序列指标（gold 来自样本自带或 E1-4 金标，`--golden`）。

用法：
    python scripts/run_agent_trace_eval.py --limit 3        # 冒烟
    python scripts/run_agent_trace_eval.py                 # complex_20 全量
    python scripts/run_agent_trace_eval.py --dataset dev_100.jsonl --limit 20
    python scripts/run_agent_trace_eval.py --golden        # E1-4 agent 金标（30 条标注真值）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import load_jsonl  # noqa: E402
from evals.runners.agent_trace import load_agent_golden, run_agent_trace  # noqa: E402

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
OUT_DIR = PROJECT_ROOT / "test_report"
GOLDEN_FILE = PROJECT_ROOT / "eval" / "agent_golden_set.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 轨迹评测（deep_agent）")
    parser.add_argument("--dataset", default="complex_20.jsonl",
                        help="金标集文件名（含 question / query_type）")
    parser.add_argument("--golden", action="store_true",
                        help=f"改用 E1-4 agent 金标（{GOLDEN_FILE.name}，标注真值）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3,
                        help="并发（deep_agent 工具链打 SiliconFlow，过大触发 429）")
    parser.add_argument("--no-derive", action="store_true",
                        help="不用规则派生 expected_tools（只出客观指标）")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.golden:
        cases = load_agent_golden(GOLDEN_FILE)
        src = f"E1-4 金标（{GOLDEN_FILE.name}）"
    else:
        samples = load_jsonl(DATA_DIR / args.dataset)
        cases = [{"question": s.question, "query_type": s.query_type} for s in samples]
        src = args.dataset
    if args.limit:
        cases = cases[: args.limit]
    print(f"样本 {len(cases)} 条（{src}），workers={args.workers}", flush=True)

    out_dir = args.out_dir or OUT_DIR / f"agent_trace_{time.strftime('%Y%m%d_%H%M%S')}"
    summary = run_agent_trace(
        cases, workers=args.workers,
        derive_expected=not args.no_derive, out_dir=out_dir,
    )

    print(f"\n=== Agent 轨迹指标（详见 {out_dir}）===")
    print(f"  样本 {summary['n']}  失败 {summary['errors']}  耗时 {summary['elapsed_s']}s")
    ps = summary["plan_steps"]
    print(f"  规划步数 均值 {ps['mean']}（min {ps['min']}/max {ps['max']}），"
          f"多步占比 {ps['multi_step_rate']:.1%}")
    print(f"  工具使用 {summary['tool_usage']}")
    print(f"  每步状态 {summary['step_status']}")
    print(f"  反思结论 {summary['reflection_verdict']}")
    ts = summary.get("tool_selection")
    if ts:
        print(f"  工具选择(弱标注 n={ts['n_gold']}): "
              f"P={ts['precision_mean']} R={ts['recall_mean']} F1={ts['f1_mean']} "
              f"全覆盖={ts['full_coverage_rate']:.1%}")
    tq = summary.get("tool_sequence")
    if tq:
        print(f"  工具序列(E1-4 金标 n={tq['n_gold']}): "
              f"有序覆盖={tq['ordered_coverage_rate']:.1%} "
              f"严格等序={tq['exact_sequence_rate']:.1%}")


if __name__ == "__main__":
    main()
