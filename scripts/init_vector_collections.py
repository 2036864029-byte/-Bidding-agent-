"""在目标 Milvus（本地或云托管）上创建本项目所需的空集合。

## 为什么单独一个脚本

`python -m public_kb --init` 是「解析 PDF + 向量化 + 入库」的全流程，用于建库；这里只需要
**建集合与索引**（schema / 索引 / BM25 Function 三样与本地完全一致），数据由
`scripts/copy_vector_collection.py` 从现有库搬过去 —— 不重算 embedding。

Schema 单一来源：本脚本不重新声明字段，而是直接复用 `MilvusStoreManager` 的
`_build_schema()` / `_build_index_params()` / `_validate_collection_contract()`。
一旦字段定义改动，这里自动跟随，不会出现「脚本建的集合」与「代码认的集合」不一致。

## 云托管档位

`VECTOR_BACKEND=cloud` 时 dense 索引默认用 AUTOINDEX —— 这是为了对齐 Zilliz **迁移流程**的
产物（迁入的集合会被自动建成 AUTOINDEX），不是平台限制：实测 Zilliz Serverless 上
HNSW / IVF_FLAT / AUTOINDEX 三种索引都能建。切换档位：

    # .env.cloud（不入仓，见 .gitignore）
    VECTOR_BACKEND=cloud
    MILVUS_URI=https://in03-xxxx.serverless.<region>.zillizcloud.com
    MILVUS_TOKEN=<API Key>
    MILVUS_COLLECTION=public_kb_chunked_v2
    ENABLE_MILVUS_BM25=true

    # 先看要建什么，不写入
    python scripts/init_vector_collections.py --env-file .env.cloud --dry-run

    # 真建
    python scripts/init_vector_collections.py --env-file .env.cloud

`--env-file` 用 override=True 覆盖进程环境，**不会**改动仓库里的 `.env`；不带该参数时
读 `.env`（本地档位），行为与平时启动一致。

## 为什么不自动丢掉已有集合

默认遇到同名集合就报错退出（与 `initialize_collection` 的默认一致）。要重建必须显式
`--recreate`，且集合名必须落在实验前缀 `public_kb_*` 里（`Settings.milvus_experiment_prefix`），
生产集合名 `public_kb` 与 `MILVUS_COLLECTION` 当前值同样受保护——云端误删无法靠本地恢复。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_env(path: str) -> None:
    """把指定 env 文件载入进程环境（覆盖同名项），必须在构造 Settings 之前调用。"""
    if not path:
        return
    from dotenv import load_dotenv

    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"找不到 env 文件：{env_path}")
    load_dotenv(env_path, override=True)
    print(f"已加载 env 文件：{env_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="创建本项目所需的 Milvus 集合（可指向云托管）")
    ap.add_argument("--env-file", default="", help="额外的 env 文件（覆盖同名项），如 .env.cloud")
    ap.add_argument("--name", default="", help="集合名，默认取 MILVUS_COLLECTION")
    ap.add_argument("--recreate", action="store_true", help="已存在时先删除再建（仅限 public_kb_* 实验集合）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要创建的 schema/索引，不写入")
    ap.add_argument("--skip-load", action="store_true", help="创建后不执行 load_collection")
    args = ap.parse_args()

    _load_env(args.env_file)

    from core.config.settings import Settings
    from core.embeddings import get_embeddings
    from core.vectorstore.milvus_client import MilvusStoreManager

    overrides = {}
    if args.name:
        overrides["collection_name"] = args.name
    settings = Settings(**overrides)
    name = settings.collection_name

    print("=" * 72)
    print(f"目标集合 : {name}")
    print(f"后端档位 : {settings.vector_backend or 'local'}")
    print(f"URI      : {settings.resolved_milvus_uri}")
    print(f"token    : {'已配置' if settings.milvus_token else '未配置'}")
    print(f"dense    : {settings.milvus_dense_index_type} / {settings.milvus_sparse_index_type}")
    print(f"BM25     : {settings.enable_bm25}（analyzer={settings.bm25_analyzer_type}）")
    print("=" * 72)

    manager = MilvusStoreManager(settings, get_embeddings(settings))
    client = manager._client  # noqa: SLF001 - 运维脚本，需要复用私有 schema 构建器

    try:
        server_version = client.get_server_version()
    except Exception as exc:  # noqa: BLE001 - 拿不到版本不影响主流程
        server_version = f"<unknown: {type(exc).__name__}>"
    print(f"服务端版本: {server_version}")

    exists = bool(client.has_collection(name))
    print(f"集合现状  : {'已存在' if exists else '不存在'}")

    schema = manager._build_schema()  # noqa: SLF001
    index_params = manager._build_index_params()  # noqa: SLF001

    if args.dry_run:
        print("\n-- 将要声明的字段 --")
        for field in schema.fields:
            extra = ""
            params = getattr(field, "params", None) or {}
            if params.get("max_length"):
                extra = f" max_length={params['max_length']}"
            if getattr(field, "is_primary", False):
                extra += " PRIMARY"
            print(f"  {field.name:<22} {field.dtype}{extra}")
        print("\n-- 将要声明的 Function --")
        for fn in schema.functions or []:
            print(f"  {fn.name} {fn.type} {fn.input_field_names} -> {fn.output_field_names}")
        print("\n-- 将要创建的索引 --")
        for item in index_params:
            print(f"  {item}")
        print("\n(dry-run：未写入任何内容)")
        return 0

    if exists and not args.recreate:
        print("\n集合已存在，未做任何改动。要重建请加 --recreate（仅限 public_kb_* 实验集合）。")
        return 1
    if exists:
        manager._assert_recreate_is_safe()  # noqa: SLF001
        client.drop_collection(name)
        print(f"已删除已有集合：{name}")

    client.create_collection(collection_name=name, schema=schema, index_params=index_params)
    print(f"已创建集合：{name}")

    manager._validate_collection_contract()  # noqa: SLF001
    print("Schema 契约校验通过（字段 / BM25 Function / 索引齐全）")

    if not args.skip_load:
        client.load_collection(name)
        print("已 load 集合（可立即检索）")

    stats = client.get_collection_stats(name)
    print(f"当前行数：{stats.get('row_count')}")
    print("\n下一步：python scripts/copy_vector_collection.py --env-file .env.cloud --source-env .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
