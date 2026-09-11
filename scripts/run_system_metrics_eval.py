"""系统指标评测入口（薄 CLI）— 逻辑在 `evals/runners/system`。

在独立进程里跑 N 题，采集进程内 Prometheus 指标（延迟/token/错误/降级），出系统段报告。

用法：
    python scripts/run_system_metrics_eval.py --limit 5        # 冒烟
    python scripts/run_system_metrics_eval.py                  # dev_100 全量
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import load_jsonl  # noqa: E402
from evals.runners.system import run_system_eval  # noqa: E402

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
OUT_DIR = PROJECT_ROOT / "test_report"


def main() -> None:
    parser = argparse.ArgumentParser(description="系统指标评测（E2-4）")
    parser.add_argument("--dataset", default="dev_100.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    samples = load_jsonl(DATA_DIR / args.dataset)
    if args.limit:
        samples = samples[: args.limit]
    cases = [{"question": s.question, "query_type": s.query_type, "sample_id": f"{i:04d}"}
             for i, s in enumerate(samples)]
    print(f"样本 {len(cases)} 条（{args.dataset}），workers={args.workers}", flush=True)

    out_dir = args.out_dir or OUT_DIR / f"system_metrics_{time.strftime('%Y%m%d_%H%M%S')}"
    summary = run_system_eval(cases, workers=args.workers, out_dir=out_dir)

    print(f"\n=== 系统指标（详见 {out_dir}）===")
    print(f"  样本 {summary['n']}  失败 {summary['errors']}  耗时 {summary['elapsed_s']}s")
    for name, v in (summary.get("llm_latency") or {}).items():
        print(f"  LLM {name}: n={v['count']} mean={v['mean_ms']}ms p95={v['p95_ms']}ms")
    for name, v in (summary.get("branch_latency") or {}).items():
        print(f"  分支 {name}: n={v['count']} mean={v['mean_ms']}ms p95={v['p95_ms']}ms")
    print(f"  token {summary.get('llm_tokens')}")
    print(f"  LLM 失败 {summary.get('llm_errors') or '无'}  "
          f"护栏失败 {summary.get('guardrail_fails') or '无'}  "
          f"检索降级 {summary.get('recall_fallbacks') or '无'}")


if __name__ == "__main__":
    main()
