# 功能：切换集合后冒烟——验证 RAG 检索链路从 public_kb_chunked_v2 返回真实块。
"""集合切换冒烟测试：真实 embeddings(硅基流动 bge-m3) + 本地 Milvus。

用法：
    python scripts/diag_retrieval_smoke.py "政府采购公开招标公告期限是多少天？"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根

from core.config.settings import Settings
from core.embeddings import get_embeddings
from core.vectorstore.milvus_client import MilvusStoreManager
from public_kb.retrieval.retriever import HybridRetriever
from public_kb.retrieval.reranker import SiliconFlowReranker


def main(argv: list[str] | None = None) -> int:
    settings = Settings()
    print(f"Settings().collection_name = {settings.collection_name!r}")
    print(f"  enable_bm25={settings.enable_bm25}, strict_hybrid={settings.strict_hybrid_validation}")

    embeddings = get_embeddings(settings)
    manager = MilvusStoreManager(settings, embeddings)
    loaded = manager.load_existing()
    print(f"load_existing={loaded}")
    if not loaded:
        print("集合不存在，无法冒烟")
        return 1
    print(f"集合行数: {manager.collection.get_collection_stats(settings.collection_name).get('row_count')}")

    reranker = SiliconFlowReranker(
        model=settings.reranker_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )
    retriever = HybridRetriever(
        vector_store=manager.store,
        collection=manager.collection,
        embeddings=embeddings,
        settings=settings,
        reranker=reranker,
    )

    question = argv[0] if argv else "政府采购公开招标的公告期限是多少天？"
    result = retriever.retrieve(question)
    diag = result.diagnostics
    print(f"\n问题: {question}")
    print(f"retrieval_mode={diag.retrieval_mode}  fallback_reason={diag.fallback_reason}")
    print(f"命中 {len(result.docs)} 条：")
    for doc, score in result.docs:
        meta = doc.metadata
        snippet = doc.page_content[:60].replace("\n", " ")
        print(
            f"  [{meta.get('doc_type'):<14}] {meta.get('doc_name','')[:30]:<32} "
            f"chunk_idx={meta.get('chunk_index')} score={score:.4f} :: {snippet}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
