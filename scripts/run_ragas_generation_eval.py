"""S1 / E2-2 Ragas 生成层评测入口（薄 CLI）— 逻辑在 `evals/runners/generation`。

对 Layer 0（top-11）与 Layer 1（统一轻量补检 ~17.7 块）的生成质量做逐题配对对比，
判断扩窗后生成侧是否被稀释。指标：faithfulness / answer_relevancy / context_precision。

判定模型默认智谱（glm-4-plus），env 可换：
    JUDGE_MODEL / JUDGE_BASE_URL / JUDGE_API_KEY
未配 JUDGE_API_KEY 时回退 kb_generation 槽位（项目默认 deepseek-chat）。

用法：
    python scripts/run_ragas_generation_eval.py --limit 4          # 冒烟
    python scripts/run_ragas_generation_eval.py                    # 全量 dev_100
    python scripts/run_ragas_generation_eval.py --type multi_hop   # 只看多跳
    python scripts/run_ragas_generation_eval.py --workers 8 --batch-size 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from evals.datasets.rag import load_jsonl  # noqa: E402
from evals.runners.generation import JudgeConfig, run_generation_eval  # noqa: E402

OUT_DIR = PROJECT_ROOT / "test_report"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S1/E2-2 Ragas 生成层评测（Layer0 vs Layer1）")
    parser.add_argument("--dataset", type=Path,
                        default=PROJECT_ROOT / "DATA" / "eval_rag" / "dev_100.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--type", default=None,
                        help="只跑某 query_type（multi_hop / single_hop）")
    parser.add_argument("--workers", type=int, default=4,
                        help="采集阶段并发（检索+生成），默认 4")
    parser.add_argument("--batch-size", type=int, default=12,
                        help="Ragas 判定批内并发，默认 12")
    parser.add_argument("--metrics", default=None,
                        help="判定指标子集（逗号分隔，如 --metrics context_recall）；"
                             "缺省=全部（含 context_recall）。支持增量补跑合并")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="产物目录（默认 test_report/ragas_s1_<ts>）")
    parser.add_argument("--shard", default=None,
                        help="进程级分片 K/N（只处理 cases[K::N]），配合多进程并行绕开 "
                             "ragas 内部并发瓶颈；结果用 --merge-shards 合并")
    parser.add_argument("--merge-shards", action="store_true",
                        help="合并 --out-dir 下 shard_*/ 的分片产物")
    parser.add_argument("--no-resume", action="store_true",
                        help="忽略已采集 samples.jsonl，重跑采集")
    args = parser.parse_args()

    if args.merge_shards:
        from core.config.settings import Settings

        from evals.runners.generation import merge_shards

        if args.out_dir is None:
            parser.error("--merge-shards 需要 --out-dir")
        Settings()  # 触发 .env 加载，供 merge_shards 里的 JudgeConfig 读 judge 信息
        summary = merge_shards(args.out_dir)
        print(f"merged -> {args.out_dir}  配对 n={summary['n_paired']}", flush=True)
        for m in summary["metrics"]:
            d = summary["paired_delta"][m]
            print(f"  {m:18s} L0={summary['per_layer']['l0']['mean'][m]:.4f} "
                  f"L1={summary['per_layer']['l1']['mean'][m]:.4f} Δ={d['mean']:+.4f}",
                  flush=True)
        return

    cases = load_jsonl(args.dataset)
    if args.type:
        cases = [c for c in cases if c.query_type == args.type]
    if args.limit:
        cases = cases[: args.limit]
    # dev_100 无 sample_id：按过滤/截断后的顺序位置派生（断点续跑稳定）
    cases = [{**c.model_dump(), "sample_id": f"{i:04d}"}
             for i, c in enumerate(cases)]

    if args.out_dir is None:
        out_dir = OUT_DIR / f"ragas_s1_{time_now()}"
    else:
        out_dir = args.out_dir

    # 先构造 Settings 触发 .env 加载，再读 judge 配置（否则读到空 api_key）
    from core.config.settings import Settings

    Settings()
    cfg = JudgeConfig()
    print(f"judge: model={cfg.model} base_url={cfg.base_url} "
          f"configured={bool(cfg.api_key)}", flush=True)

    shard = None
    if args.shard:
        k, n = args.shard.split("/")
        shard = (int(k), int(n))

    summary = run_generation_eval(
        cases,
        workers=args.workers,
        batch_size=args.batch_size,
        metrics=([m.strip() for m in args.metrics.split(",") if m.strip()]
                 if args.metrics else None),
        shard=shard,
        resume=not args.no_resume,
        out_dir=out_dir,
    )

    print("\n=== S1 结果（详见 " + str(out_dir) + "）===", flush=True)
    print(f"配对 n={summary['n_paired']}  ragas 判定模型={cfg.model}", flush=True)
    for m in summary["metrics"]:
        d = summary["paired_delta"][m]
        dstr = f"{d['mean']:+.4f}" if d["mean"] == d["mean"] else "N/A"
        print(f"  {m:18s} L0={summary['per_layer']['l0']['mean'][m]:.4f} "
              f"L1={summary['per_layer']['l1']['mean'][m]:.4f} "
              f"Δ={dstr} 变差{d['worsened']}/不变{d['unchanged']}/变好{d['improved']}",
              flush=True)


def time_now() -> str:
    import time

    return time.strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    main()
