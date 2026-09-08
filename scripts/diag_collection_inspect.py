# 功能：诊断本地 Milvus 集合——schema/索引/行数（阶段 3 后核实用）。
"""检查本地 Milvus 集合 public_kb_chunked_v2 的结构与存量。

用法：
    python scripts/diag_collection_inspect.py [--collection public_kb_chunked_v2]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根

from pymilvus import MilvusClient

from core.config.settings import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="诊断本地 Milvus 集合")
    ap.add_argument("--collection", default="public_kb_chunked_v2")
    args = ap.parse_args(argv)

    settings = Settings()
    client = MilvusClient(
        uri=settings.resolved_milvus_uri,
        timeout=settings.milvus_timeout,
    )
    print(f"连接: {settings.resolved_milvus_uri}  (settings.enable_bm25={settings.enable_bm25})")
    print(f"全部集合: {client.list_collections()}")

    name = args.collection
    if not client.has_collection(name):
        print(f"集合 {name} 不存在！")
        return 1

    stats = client.get_collection_stats(name)
    print(f"\n== {name} 行数 ==\n  get_collection_stats.row_count = {stats.get('row_count')}")

    # count(*) 复核 + doc_type 分布（核对两路口径一致）
    cnt = client.query(
        collection_name=name,
        filter="id >= 0",
        output_fields=["count(*)"],
    )
    print(f"  query count(*) = {cnt[0]['count(*)'] if cnt else 'N/A'}")
    rows = client.query(
        collection_name=name,
        filter="id >= 0",
        output_fields=["doc_type"],
        limit=16384,
    )
    by_type: dict[str, int] = {}
    for r in rows or []:
        t = r.get("doc_type") or "(empty)"
        by_type[t] = by_type.get(t, 0) + 1
    print(f"  doc_type 分布: {dict(sorted(by_type.items(), key=lambda kv: -kv[1]))}")

    desc = client.describe_collection(name)
    print("\n== schema 字段 ==")
    for f in desc.get("fields", []):
        name_ = f.get("name")
        dtype = f.get("type")
        extra = ""
        params = f.get("params") or {}
        if "max_length" in params:
            extra = f" max_length={params['max_length']}"
        if "dim" in params:
            extra = f" dim={params['dim']}"
        is_dynamic = f.get("is_dynamic", False)
        print(f"  {name_:<18} {str(dtype):<24}{extra}{'  [dynamic]' if is_dynamic else ''}")
    funcs = desc.get("functions") or []
    print(f"\n== functions（BM25）==  count={len(funcs)}")
    for fn in funcs:
        print(f"  {fn}")

    print("\n== 索引 ==")
    for idx in client.list_indexes(name):
        idx_desc = client.describe_index(name, idx)
        print(f"  {json.dumps(idx_desc, ensure_ascii=False, indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
