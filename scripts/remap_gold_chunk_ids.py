"""金标 chunk_id 重映射入口（E1-5）— 逻辑在 `evals/datasets/remap`。

拉当前 Milvus 集合 → 用金标段落文本离线匹配 → 回填 `ground_truth_chunk_ids`
（当前主键 id）与 `ground_truth_chunk_uids`（内容派生，稳定）。

用法：
    python scripts/remap_gold_chunk_ids.py --dry-run     # 只报告，不写
    python scripts/remap_gold_chunk_ids.py               # 回填全部金标集（自动备份）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import dump_jsonl, load_jsonl  # noqa: E402
from evals.datasets.remap import build_index, remap_samples  # noqa: E402

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
DATASETS = ("testset_1000_local.jsonl", "dev_100.jsonl",
            "holdout_200.jsonl", "complex_20.jsonl")


def _pull_collection() -> list[dict]:
    from core.config.settings import Settings
    from pymilvus import MilvusClient

    s = Settings()
    client = MilvusClient(uri=f"http://{s.milvus_host}:{s.milvus_port}")
    return client.query(collection_name=s.collection_name, filter="id >= 0",
                        output_fields=["id", "chunk_uid", "text"], limit=16384)


def main() -> None:
    parser = argparse.ArgumentParser(description="金标 chunk_id 重映射（E1-5）")
    parser.add_argument("--dataset", action="append", default=None,
                        help="只处理指定文件（可重复）；缺省=全部")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    rows = _pull_collection()
    index = build_index(rows)
    print(f"集合块数 {len(rows)}，索引就绪（exact keys={len(index['exact'])}）", flush=True)

    targets = args.dataset or DATASETS
    stamp = time.strftime("%Y%m%d_%H%M%S")
    manifest = {"stamp": stamp, "collection_rows": len(rows), "datasets": {}}

    for name in targets:
        path = DATA_DIR / name
        samples = load_jsonl(path)
        new, stats = remap_samples(samples, index)
        manifest["datasets"][name] = stats
        rate = (stats["exact"] + stats["contained"]) / stats["passages"] if stats["passages"] else 0
        print(f"  {name}: 段落 {stats['passages']} exact {stats['exact']} "
              f"contained {stats['contained']} none {stats['none']} "
              f"→ 命中率 {rate:.1%}", flush=True)
        if args.dry_run:
            continue
        if not args.no_backup:
            shutil.copy(path, path.with_suffix(f".jsonl.bak_{stamp}"))
        dump_jsonl(new, path)

    if not args.dry_run:
        out = DATA_DIR / f"remap_manifest_{stamp}.json"
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"回填完成，manifest → {out}")


if __name__ == "__main__":
    main()
