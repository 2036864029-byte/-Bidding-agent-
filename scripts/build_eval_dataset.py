"""构建评测金标集：分层抽样 + holdout 切分（E1-1 / E1-2）。

从 `DATA/eval_rag/testset_1000_local.jsonl` 生成：
  - `dev_<N>.jsonl`     分层抽样的开发集（调 prompt / 阈值用）
  - `holdout_<M>.jsonl` 门禁用留出集（调参时不可见）
  - `manifest.json`     种子 / 规模 / 分层分布 / 源文件 / git commit（可复现凭据）

用法：
    python scripts/build_eval_dataset.py                  # 默认 dev=100 / holdout=200
    python scripts/build_eval_dataset.py --dev 200 --holdout 300
    python scripts/build_eval_dataset.py --seed 7         # 换种子（会改变抽样结果）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import (  # noqa: E402
    distribution,
    dump_jsonl,
    load_jsonl,
    split_holdout,
    stratified_sample,
)

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
SOURCE = DATA_DIR / "testset_1000_local.jsonl"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="构建评测金标集")
    parser.add_argument("--dev", type=int, default=100, help="开发集条数（分层抽样）")
    parser.add_argument("--holdout", type=int, default=200, help="留出集条数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"金标源文件不存在: {SOURCE}")

    samples = load_jsonl(SOURCE)
    print(f"源金标集: {len(samples)} 条 {distribution(samples)}")

    dev = stratified_sample(samples, args.dev, seed=args.seed)
    # holdout 从源全量里分层切，保证与 dev 的构造方式一致、可独立复现
    _, holdout = split_holdout(samples, args.holdout, seed=args.seed)

    dev_path = DATA_DIR / f"dev_{args.dev}.jsonl"
    holdout_path = DATA_DIR / f"holdout_{args.holdout}.jsonl"
    dump_jsonl(dev, dev_path)
    dump_jsonl(holdout, holdout_path)

    manifest = {
        "source": SOURCE.name,
        "source_size": len(samples),
        "seed": args.seed,
        "git_commit": _git_commit(),
        "dev": {"file": dev_path.name, "size": len(dev), "distribution": distribution(dev)},
        "holdout": {
            "file": holdout_path.name, "size": len(holdout),
            "distribution": distribution(holdout),
        },
    }
    manifest_path = DATA_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"dev     : {dev_path.name}  {len(dev)} 条 {distribution(dev)}")
    print(f"holdout : {holdout_path.name}  {len(holdout)} 条 {distribution(holdout)}")
    print(f"manifest: {manifest_path.name}（seed={args.seed} commit={manifest['git_commit']}）")


if __name__ == "__main__":
    main()
