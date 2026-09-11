"""E4-1 线上 trace 采样回灌金标（薄 CLI）— 逻辑在 `evals/trace_feedback`。

从 Langfuse(ClickHouse) 采样近期 trace → 规则标记异常 → 产出标注候选；
人工复核（把 `review_status` 改为 `reviewed`）后 `--merge` 追加进金标集。

用法：
    # 1) 采样：拉取近 48h 异常 trace，写候选文件（test_report/，gitignored）
    python scripts/sample_traces_for_golden.py --sample
    python scripts/sample_traces_for_golden.py --sample --include-clean --limit 50

    # 2) 人工复核候选文件（补 key_entities、订正 expected_*、置 review_status=reviewed）

    # 3) 合并：把复核过的候选追加进 eval/agent_golden_set.json（去重）
    python scripts/sample_traces_for_golden.py --merge test_report/trace_golden_candidates.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.trace_feedback import (  # noqa: E402
    load_ch_cfg,
    merge_into_golden,
    sample_candidates,
)

DEFAULT_OUT = PROJECT_ROOT / "test_report"
GOLDEN = PROJECT_ROOT / "eval" / "agent_golden_set.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Langfuse trace 采样回灌金标（E4-1）")
    parser.add_argument("--sample", action="store_true", help="采样模式（默认）")
    parser.add_argument("--merge", type=Path, default=None, help="合并复核过的候选文件")
    parser.add_argument("--since-hours", type=int, default=48)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--slow-ms", type=int, default=15000)
    parser.add_argument("--root-name", default="POST /chat", help="根 span 名前缀")
    parser.add_argument("--include-clean", action="store_true", help="连正常 trace 一起采")
    parser.add_argument("--deploy-env", type=Path, default=None, help="Langfuse .env 路径")
    parser.add_argument("--out", type=Path, default=None, help="候选输出文件")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    args = parser.parse_args()

    if args.merge:
        data = json.loads(args.merge.read_text(encoding="utf-8"))
        cands = data.get("candidates", data) if isinstance(data, dict) else data
        res = merge_into_golden(cands, args.golden)
        print(f"合并 → {args.golden}")
        print(f"  新增 {res['added']} 条；跳过 {res['skipped']} 条")
        for q in res["added_q"]:
            print(f"    + {q[:50]}")
        for s in res["skipped_detail"][:20]:
            print(f"    - [{s['reason']}] {s['q']}")
        return

    cfg = load_ch_cfg(args.deploy_env)
    cands = sample_candidates(
        cfg, since_hours=args.since_hours, limit=args.limit,
        slow_ms=args.slow_ms, root_name=args.root_name,
        include_clean=args.include_clean,
    )
    out = args.out or (DEFAULT_OUT / f"trace_golden_candidates_{time.strftime('%Y%m%d_%H%M%S')}.json")
    payload = {
        "description": "E4-1 trace 回灌候选（Langfuse 采样）。复核后置 review_status=reviewed 再用 --merge 合并。",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "since_hours": args.since_hours,
        "slow_ms": args.slow_ms,
        "n": len(cands),
        "candidates": cands,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"采样 {len(cands)} 条候选 → {out}")
    for c in cands:
        print(f"  [{','.join(c['trace_ref']['flagged'])}] {c['query_type']:<12} "
              f"{c['expected_sequence']} | {c['q'][:40]}")


if __name__ == "__main__":
    main()
