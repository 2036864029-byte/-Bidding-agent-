"""把一个已有 Milvus 集合的数据搬到另一个 Milvus（本地 ⇄ 云托管），**不重算 embedding**。

## 为什么不是「重新入库」

重新入库 = 重新调 embedding API（花钱）+ 重新分块（chunk_uid 可能变，评测金标集作废）。
本脚本直接读**源库里已有的稠密向量**，原样写入目标库。

## 关键机制：sparse 不搬，让目标端自己算

BM25 的 `sparse_vector` 是**服务端 Function 的输出**，不是可以读写的普通字段：
读它会直接报 `not allowed to retrieve raw data of field sparse_vector`（实测 v2.6.23）。
所以这里只搬 `text` + 标量字段 + `dense vector`，目标集合的 BM25 Function 会在插入时
自动生成 sparse —— 前提是目标集合的 schema 与源一致（用
`scripts/init_vector_collections.py` 建，或由 Zilliz 迁移生成）。

## 用法

    # 1) 先建目标集合（云端）
    python scripts/init_vector_collections.py --env-file .env.cloud

    # 2) 冒烟：只搬 200 条，看通不通
    python scripts/copy_vector_collection.py --dest-env .env.cloud --limit 200 --dest-collection public_kb_smoke

    # 3) 全量
    python scripts/copy_vector_collection.py --dest-env .env.cloud

    # 只想看要搬什么、不写
    python scripts/copy_vector_collection.py --dest-env .env.cloud --dry-run

`--dest-env` 只用来读目标端的 `MILVUS_URI` / `MILVUS_TOKEN`（用 dotenv_values 读，**不污染**
进程环境）；源端始终用仓库 `.env` 的配置。也可以用 `--dest-uri` / `--dest-token` 直接指定。

## 注意

- 目标集合必须已存在且 schema 兼容，否则脚本在写入前就退出（不猜、不自动建）。
- 主键是 auto_id 时**不搬 id**（目标端会重新分配）；引用链路靠 `chunk_uid`，不受影响。
- 搬完自动对比两端行数；不一致返回非零退出码。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Milvus DataType.SPARSE_FLOAT_VECTOR 的枚举值；用数值避免依赖具体 pymilvus 版本导出名
_SPARSE_DTYPE = 104


def _read_env_file(path: str) -> dict[str, str]:
    """只读取，不改进程环境（源端还要用 .env 的值）。"""
    if not path:
        return {}
    from dotenv import dotenv_values

    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"找不到 env 文件：{env_path}")
    return {k: v for k, v in dotenv_values(env_path).items() if v is not None}


def _describe(client, name: str) -> dict:
    """描述集合，并归一化成脚本需要的几项。"""
    desc = client.describe_collection(name)
    fields = desc.get("fields", []) or []
    pk = ""
    for field in fields:
        if field.get("is_primary"):
            pk = field.get("name", "")
            break
    if not pk and fields:
        pk = fields[0].get("name", "")

    fn_outputs: set[str] = set()
    for fn in desc.get("functions", []) or []:
        fn_outputs.update(fn.get("output_field_names") or [])

    dense = ""
    dim = 0
    copyable: list[str] = []
    for field in fields:
        name_ = field.get("name", "")
        dtype = field.get("type")
        params = field.get("params", {}) or {}
        if dtype == _SPARSE_DTYPE or name_ in fn_outputs:
            continue  # 服务端生成，读不回来也不能直接写
        if name_ == pk and desc.get("auto_id", True):
            continue  # 目标端重新分配主键
        if dtype == 101 and not dense:  # FLOAT_VECTOR
            dense = name_
            dim = int(params.get("dim") or 0)
        copyable.append(name_)

    return {
        "fields": fields,
        "pk": pk,
        "auto_id": bool(desc.get("auto_id", True)),
        "copy_fields": copyable,
        "dense": dense,
        "dim": dim,
        "functions": fn_outputs,
    }


def _schema_problems(src: dict, dst: dict) -> list[str]:
    """目标端必须覆盖源端所有可搬字段，且 dense 维度一致。"""
    problems: list[str] = []
    dst_map = {f.get("name"): f for f in dst["fields"]}
    for name in src["copy_fields"]:
        if name not in dst_map:
            problems.append(f"目标缺少字段 {name!r}")
    if src["dim"] and dst["dim"] and src["dim"] != dst["dim"]:
        problems.append(f"dense 维度不一致：源 {src['dim']} vs 目标 {dst['dim']}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="跨 Milvus 搬运集合数据（不重算 embedding）")
    ap.add_argument("--source-collection", default="", help="源集合，默认取 .env 的 MILVUS_COLLECTION")
    ap.add_argument("--dest-collection", default="", help="目标集合，默认与源同名")
    ap.add_argument("--dest-env", default="", help="目标端 env 文件（读 MILVUS_URI / MILVUS_TOKEN）")
    ap.add_argument("--dest-uri", default="", help="目标端 URI（优先于 --dest-env）")
    ap.add_argument("--dest-token", default="", help="目标端 token（优先于 --dest-env）")
    ap.add_argument("--batch-size", type=int, default=300)
    ap.add_argument("--limit", type=int, default=0, help="最多搬多少条（冒烟用），0 = 全部")
    ap.add_argument("--dry-run", action="store_true", help="只校验 schema 与行数，不写入")
    ap.add_argument("--wait-timeout", type=float, default=180.0, help="搬运后等待行数一致的秒数")
    ap.add_argument("--force", action="store_true", help="schema 不一致也继续（危险）")
    args = ap.parse_args()

    from pymilvus import MilvusClient

    from core.config.settings import Settings

    src_settings = Settings()
    src_name = args.source_collection or src_settings.collection_name
    dst_name = args.dest_collection or src_name

    dest_vars = _read_env_file(args.dest_env)
    dst_uri = (
        args.dest_uri
        or dest_vars.get("MILVUS_URI")
        or os.getenv("MILVUS_URI")
        or src_settings.resolved_milvus_uri
    )
    dst_token = args.dest_token or dest_vars.get("MILVUS_TOKEN") or os.getenv("MILVUS_TOKEN") or ""

    src = MilvusClient(
        uri=src_settings.resolved_milvus_uri,
        token=src_settings.milvus_token or None,
        timeout=src_settings.milvus_timeout,
    )
    dst = MilvusClient(uri=dst_uri, token=dst_token or None, timeout=60)

    print("=" * 72)
    print(f"源   : {src_name}  @ {src_settings.resolved_milvus_uri}")
    print(f"目标 : {dst_name}  @ {dst_uri}")
    print(f"目标凭据: {'已配置' if dst_token else '未配置'}")
    print("=" * 72)

    try:
        print(f"目标服务端版本: {dst.get_server_version()}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 取目标版本失败: {type(exc).__name__}: {exc}")
        return 2

    if not src.has_collection(src_name):
        print(f"源集合不存在：{src_name}")
        return 2
    if not dst.has_collection(dst_name):
        print(f"目标集合不存在：{dst_name}（先用 scripts/init_vector_collections.py 建）")
        return 2

    src_desc = _describe(src, src_name)
    dst_desc = _describe(dst, dst_name)
    problems = _schema_problems(src_desc, dst_desc)
    if problems:
        print("\n❌ 两端 schema 不兼容：")
        for item in problems:
            print(f"  - {item}")
        if not args.force:
            print("（不写入。确认要硬来时加 --force）")
            return 2
        print("（--force 已指定，继续）")

    print(f"\n可搬字段({len(src_desc['copy_fields'])})：{', '.join(src_desc['copy_fields'])}")
    print(f"服务端生成、不搬：{', '.join(sorted(src_desc['functions'])) or '（无）'}")
    src.load_collection(src_name)
    total = int(src.get_collection_stats(src_name).get("row_count") or 0)
    planned = min(total, args.limit) if args.limit else total
    print(f"源行数：{total}   计划搬运：{planned}")

    if args.dry_run:
        print("\n(dry-run：未写入任何内容)")
        return 0

    if args.limit:
        dst_before = int(dst.get_collection_stats(dst_name).get("row_count") or 0)
        print(f"目标已有行数：{dst_before}（追加写入，不会自动清空）")

    # ── 搬运 ──
    # output_fields 用 "*"：动态字段（article_number / question / content_type 等）只有选它
    # 才会返回，而项目写入时把它们当普通 key 放顶层（见 _build_records），丢了会削弱引用可读性。
    # 实测 v2.6.23 的 "*" 已自动排除 Function 输出字段（sparse_vector 读不出来，也不该读）；
    # 但主键会被强制返回，必须自己剔除 —— 它就是「多传一个字段」报错的来源。
    drop_keys = set(src_desc["functions"])
    if src_desc["auto_id"]:
        drop_keys.add(src_desc["pk"])
    moved = 0
    started = time.time()
    iterator = src.query_iterator(
        collection_name=src_name,
        batch_size=args.batch_size,
        limit=planned,
        output_fields=["*"],
    )
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            rows = [
                {k: v for k, v in row.items() if k not in drop_keys}
                for row in batch
            ]
            dst.insert(dst_name, rows)
            moved += len(rows)
            elapsed = time.time() - started
            rate = moved / elapsed if elapsed > 0 else 0
            print(f"  已写入 {moved}/{planned}  ({rate:.0f} 行/秒)", end="\r", flush=True)
    finally:
        iterator.close()
    print()
    dst.flush(dst_name)
    print(f"搬运完成：{moved} 行，用时 {time.time() - started:.1f}s")

    # ── 校验 ──
    deadline = time.time() + args.wait_timeout
    last = -1
    while time.time() < deadline:
        last = int(dst.get_collection_stats(dst_name).get("row_count") or 0)
        if last >= planned:
            break
        time.sleep(3)
    print(f"目标行数：{last}")
    if last < planned:
        print(f"⚠ 行数未达预期（{last} < {planned}），索引构建可能仍在进行，稍后复查")
        return 1
    print("✅ 行数校验通过")
    from core.vectorstore.milvus_client import MilvusStoreManager  # noqa: F401  仅提示下一步

    print("下一步：在目标端跑一次真实检索（scripts/diag_retrieval_smoke.py 或 /chat）确认召回正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
