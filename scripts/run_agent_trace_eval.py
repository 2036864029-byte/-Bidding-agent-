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
                        help=f"改用标注金标（默认 {GOLDEN_FILE.name}，配 --golden-file 换）")
    parser.add_argument("--golden-file", type=Path, default=GOLDEN_FILE,
                        help="标注金标文件；带文件集用 eval/file_golden_set.json "
                             "（其 items 带 files 字段 → 自动装载 fixture 会话）")
    parser.add_argument("--query-type", default="",
                        help="只跑指定 query_type（如 no_file_needed 专门压误入率）")
    parser.add_argument("--mode", choices=["node", "graph"], default="node",
                        help="node=只跑生产节点（量 agent 内部规划）；"
                             "graph=走完整 AgentGraph（量路由/误入率，需 CAPABILITY_ENABLED=deep_agent）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3,
                        help="并发（deep_agent 工具链打 SiliconFlow，过大触发 429）")
    parser.add_argument("--no-derive", action="store_true",
                        help="不用规则派生 expected_tools（只出客观指标）")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.golden:
        cases = load_agent_golden(args.golden_file)
        src = f"金标（{args.golden_file.name}，标注真值）"
    else:
        samples = load_jsonl(DATA_DIR / args.dataset)
        cases = [{"question": s.question, "query_type": s.query_type} for s in samples]
        src = args.dataset
    if args.query_type:
        cases = [c for c in cases if c.get("query_type") == args.query_type]
    if args.limit:
        cases = cases[: args.limit]
    print(f"样本 {len(cases)} 条（{src}），workers={args.workers}", flush=True)

    out_dir = args.out_dir or OUT_DIR / f"agent_trace_{time.strftime('%Y%m%d_%H%M%S')}"
    summary = run_agent_trace(
        cases, workers=args.workers,
        derive_expected=not args.no_derive, out_dir=out_dir, mode=args.mode,
    )

    print(f"\n=== Agent 轨迹指标（mode={args.mode}，详见 {out_dir}）===")
    print(f"  样本 {summary['n']}  失败 {summary['errors']}  耗时 {summary['elapsed_s']}s")
    rt = summary.get("routing")
    if rt:
        print(f"  路由: 期望给 agent {rt['agent_expected']} / 实际 {rt['agent_got']}"
              f"（{rt['route_dist']}）")
        print(f"    误入率 {rt['misroute_rate']}（{rt['misroute']} 条）"
              f"  漏答率 {rt['miss_rate']}（{rt['miss']} 条）")
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
    ah = summary.get("answer_hit") or {}
    if ah.get("rate") is not None:
        print(f"  答案命中(n={ah['n']}): {ah['hit']}/{ah['n']} = {ah['rate']:.1%}")
    ci = summary.get("citation") or {}
    if ci.get("rate") is not None:
        print(f"  引用覆盖率(n={ci['n']}): {ci['cited']}/{ci['n']} = {ci['rate']:.1%}"
              f"  ← 答案引用了**实际检索到**的来源（doc_qa 结构上恒 0）")
    bt = summary.get("by_query_type") or {}
    if bt:
        print("  按 query_type:")
        for qt, b in sorted(bt.items()):
            rate = "—" if b["answer_hit_rate"] is None else f"{b['answer_hit_rate']:.0%}"
            print(f"    {qt:16s} n={b['n']:2d} 失败={b['errors']} 答案命中={rate}")


if __name__ == "__main__":
    main()
