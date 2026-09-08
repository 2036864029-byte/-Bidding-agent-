# 功能：全量断点续传入库——遍历 chunks 目录所有 documents.jsonl，单进程串行写入 Milvus。
"""全量断点续传入库驱动（阶段 3）。

遍历 ``*.documents.jsonl``，逐个 ``ingest_jsonl_with_resume``（同目录进度文件
自动跳过已写入行），并依赖 MilvusStoreManager 的 chunk_uid 去重做二次幂等。
首次 add_documents 会自动创建集合（schema 含通用标量字段 + chunk_uid/doc_type 索引）。

用法：
    python scripts/ingest_all_jsonl.py DATA/work/chunks --collection public_kb_chunked_v2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根

from core.config.settings import Settings
from core.embeddings import get_embeddings
from core.vectorstore.milvus_client import MilvusStoreManager
from public_kb.ingestion.sinks.jsonl_ingest_runner import ingest_jsonl_with_resume


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="全量断点续传入库 Milvus")
    ap.add_argument("chunks_dir", nargs="?", default="DATA/work/chunks")
    ap.add_argument("--collection", default="public_kb_chunked_v2")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument(
        "--recreate",
        action="store_true",
        help="重建集合：先清空现有实验集合再重灌（schema/索引变更后生效，需集合名在实验前缀内）",
    )
    args = ap.parse_args(argv)

    settings = Settings()
    settings.collection_name = args.collection
    manager = MilvusStoreManager(settings, get_embeddings(settings))
    if args.recreate:
        # 集合不存在时无操作；非实验前缀名会被 clear_collection 拒绝
        manager.clear_collection()
        # 进度文件一并重置——recreate 语义是"全量重灌"，旧进度会让断点续传把
        # 所有行按已写入跳过（2026-09-08 实测踩坑：残留进度 → 0 写入、集合未重建）
        chunks_dir = Path(args.chunks_dir)
        for prog in chunks_dir.glob("*.progress.json"):
            prog.unlink()
        print(
            f"已清空集合 {args.collection} 并重置 {chunks_dir} 下进度文件，将按当前配置重建……"
        )
    existed = manager.load_existing()
    print(f"集合 {args.collection} 已存在={existed}，开始遍历入库……")

    files = sorted(Path(args.chunks_dir).glob("*.documents.jsonl"))
    if not files:
        print(f"未找到 jsonl: {args.chunks_dir}/*.documents.jsonl")
        return 1

    total_inserted = 0
    total_lines = 0
    for f in files:
        r = ingest_jsonl_with_resume(f, manager, batch_size=args.batch_size, flush=False)
        total_inserted += r.inserted
        total_lines += r.total_lines
        print(f"{f.name}: 行数={r.total_lines} 写入={r.inserted}")
    # 全部批次完成后统一落盘一次（flush=False 期间未逐批 flush）
    manager.collection.flush(args.collection)
    print(
        f"完成: {len(files)} 文件, {total_lines} 行, 本次写入 {total_inserted} 条"
    )
    print(f"集合总行数约: {manager.collection.get_collection_stats(args.collection).get('row_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
