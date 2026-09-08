# 功能：Milvus 向量索引类型 → 建索引/检索参数 单一分发（2026-09-08 HNSW+BM25 重构）。
"""dense 索引类型相关的参数构建（建索引 + 检索共用）。

把 "IVF_FLAT vs HNSW vs 其他" 的差异收敛到本模块，避免建索引（milvus_client）
与检索（retriever / fallback）两处各写一套判断导致漂移：
  - build_dense_index_params：create_collection 时传给 add_index 的索引 build 参数；
  - build_dense_search_params：search / AnnSearchRequest 的 dense 检索参数
    （HNSW 用 ``ef``，IVF_FLAT 用 ``nprobe``，其余不传额外参数）。
"""

from __future__ import annotations

from core.config.settings import Settings
from core.contracts import MilvusCollectionContract


def build_dense_index_params(settings: Settings) -> dict:
    """按 settings.milvus_dense_index_type 返回 dense 索引 build 参数。

    - IVF_FLAT → {"nlist": 256}
    - HNSW     → {"M": ..., "efConstruction": ...}
    - 其他     → {}（AUTOINDEX 等服务端默认）
    """
    index_type = settings.milvus_dense_index_type
    if index_type == "IVF_FLAT":
        return {"nlist": 256}
    if index_type == "HNSW":
        return {
            "M": settings.milvus_hnsw_m,
            "efConstruction": settings.milvus_hnsw_ef_construction,
        }
    return {}


def build_dense_search_params(settings: Settings) -> dict:
    """按 settings.milvus_dense_index_type 返回 dense 检索参数字典。

    返回完整 param 结构（含 metric_type），可直接用作：
      - pymilvus search/search_params；
      - AnnSearchRequest 的 param。
    """
    contract = MilvusCollectionContract()
    index_type = settings.milvus_dense_index_type
    if index_type == "HNSW":
        params = {"ef": settings.milvus_hnsw_search_ef}
    elif index_type == "IVF_FLAT":
        params = {"nprobe": settings.nprobe}
    else:
        params = {}
    return {
        "metric_type": contract.dense_metric,
        "params": params,
    }
