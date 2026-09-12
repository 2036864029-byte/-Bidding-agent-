# -*- coding: utf-8 -*-
"""ztb 模型服务 — embedding(bge-m3) + rerank(bge-reranker-v2-m3) 一体。

为什么两个模型合并成一个服务（而不是拆两个端口）：
  现有客户端 `public_kb/tools.py` 里 reranker **复用了 embedding 的 base_url**：
      SiliconFlowReranker(model=settings.reranker_model,
                          api_key=settings.embedding_api_key,
                          base_url=settings.embedding_base_url)
  所以两者必须挂在同一个 base_url 下。一个服务同时暴露：
      POST /v1/embeddings   （OpenAI 兼容）
      POST /v1/rerank       （SiliconFlow 兼容）
  切换只改 .env 的 EMBEDDING_BASE_URL / EMBEDDING_API_KEY，**业务代码零改动**。

模型与云端一致（BAAI/bge-m3 + BAAI/bge-reranker-v2-m3，1024 维）→ 向量库无需重建。

显存：两个模型 fp16 合计约 2.5GB（L20 48GB 绰绰有余）。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, List, Optional, Union

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ztb-models")

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
API_KEY = os.getenv("MODELS_API_KEY", "")
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "8"))
RERANK_MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))

_embedder: Any = None
_reranker: Any = None
_lock = threading.Lock()
_error: Optional[str] = None


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def get_embedder():
    """懒加载 bge-m3（首次调用触发下载/加载）。"""
    global _embedder, _error
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer

            logger.info("加载 embedding 模型 %s (device=%s)", EMBED_MODEL, _device())
            _embedder = SentenceTransformer(EMBED_MODEL, device=_device())
            logger.info("embedding 模型就绪，维度=%s", _embedder.get_sentence_embedding_dimension())
    return _embedder


def get_reranker():
    """懒加载 bge-reranker-v2-m3（CrossEncoder）。"""
    global _reranker, _error
    if _reranker is not None:
        return _reranker
    with _lock:
        if _reranker is None:
            from sentence_transformers import CrossEncoder

            logger.info("加载 rerank 模型 %s (device=%s)", RERANK_MODEL, _device())
            _reranker = CrossEncoder(RERANK_MODEL, device=_device(), max_length=RERANK_MAX_LENGTH)
            logger.info("rerank 模型就绪")
    return _reranker


def _warmup() -> None:
    """后台预热：让容器起来后立刻加载模型，首次请求不必等。"""
    global _error
    try:
        get_embedder()
        get_reranker()
        logger.info("预热完成")
    except Exception as exc:  # noqa: BLE001  预热失败不致命，请求时再报
        _error = f"{type(exc).__name__}: {exc}"
        logger.exception("预热失败")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warmup, daemon=True, name="warmup").start()
    yield


app = FastAPI(title="ztb-models", version="0.1.0", lifespan=lifespan)


def _authorize(authorization: Optional[str]) -> None:
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="unauthorized")


class EmbeddingRequest(BaseModel):
    model: Optional[str] = None
    input: Union[str, List[str]]


class RerankRequest(BaseModel):
    model: Optional[str] = None
    query: str
    documents: List[str]
    top_n: Optional[int] = None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "ztb-models",
        "device": _device(),
        "embed_model": EMBED_MODEL,
        "rerank_model": RERANK_MODEL,
        "embed_ready": _embedder is not None,
        "rerank_ready": _reranker is not None,
        "error": _error,
    }


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    """OpenAI 兼容 embedding 接口（langchain OpenAIEmbeddings 直接可用）。"""
    _authorize(authorization)
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts:
        raise HTTPException(status_code=400, detail="empty input")

    vectors = get_embedder().encode(
        texts,
        batch_size=EMBED_BATCH,
        normalize_embeddings=True,   # 归一化；集合用 COSINE，等价
        show_progress_bar=False,
    )
    data = [
        {"object": "embedding", "index": i, "embedding": [float(x) for x in vec]}
        for i, vec in enumerate(vectors)
    ]
    return {
        "object": "list",
        "data": data,
        "model": req.model or EMBED_MODEL,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/rerank")
def rerank(req: RerankRequest, authorization: Optional[str] = Header(default=None)) -> dict:
    """SiliconFlow 兼容 rerank 接口（SiliconFlowReranker 直接可用）。"""
    _authorize(authorization)
    if not req.documents:
        return {"results": [], "model": req.model or RERANK_MODEL}

    pairs = [(req.query, doc) for doc in req.documents]
    scores = get_reranker().predict(pairs)
    results = [
        {"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)
    ]
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    if req.top_n:
        results = results[: int(req.top_n)]
    return {"results": results, "model": req.model or RERANK_MODEL}
