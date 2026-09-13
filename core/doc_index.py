# 功能：租户私有文档索引 — 上传文件的分块 / 向量化 / 文档内混合检索（C1）。
"""core.doc_index — 会话临时文档索引（**只进 Redis，不入公共 Milvus**）。

**为什么需要**（C1，修 B4/B5）：原 doc_qa 是「全文塞入 + 16000 字符硬截断」，
60 页文档被截断 56%，问后段直接答"文档中没有"——功能看起来是坏的；即便不截断，
中部内容也会撞 lost-in-the-middle。改为**文档内检索**后：

    上传 → 分块 → 向量化 → 落索引（本模块）
    提问 → 文档内检索 top-K 块 → 喂 LLM（字符数恒定）→ 带引用作答

**分层**：本模块只依赖 ``core``（embeddings / cache / config）+ numpy，
**不 import public_kb**（core 为基础设施层，分块由调用方完成后传入）。
需要「markdown → 索引」整链的调用方见 ``api/doc_context.py``。

**存储**：复用 ``file_store`` 的后端配置（memory | redis）与 TTL——索引与会话
文件同生命周期，且多 worker 下的跨进程共享要求完全一致（``API_WORKERS=N`` 时
worker A 建的索引必须能被 worker B 读到）。key = ``session_id|file_id``。

**检索**：稠密余弦（numpy 点积，向量在 build 时已 L2 归一）与稀疏 BM25 各自排序
→ RRF 融合（k=60，与公共 KB 检索同一约定）。BM25 的 tf/df 用 ``str.count`` /
``in`` 在检索时现算——**不做倒排索引存储**（200 块的文档约 20~40ms，而存 tf
字典会让 pickle 体积涨到十几 MB，不划算）。

**fail-open 贯穿全链**：向量化失败 / 缓存不可用 → 返回 None，调用方回退全文模式。
索引挂了只是退回旧行为，绝不让上传或提问失败。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: 索引版本 —— 融合参数 / 向量空间约定变更时递增（旧 key 自然失效）
DOC_INDEX_VERSION = "v1"

_CACHE_NAME = "doc_index"
#: 内存后端最多缓存多少份文档索引（多 worker 部署请用 redis 后端）
_MEMORY_MAXSIZE = 64

#: RRF 融合常数（与 public_kb 检索同值）
_RRF_K = 60
#: BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75

#: 低相关度告警线默认值——与 ``Settings.doc_index_min_score`` 同源（实测标定见该字段）
DEFAULT_MIN_SCORE = 0.45

_CJK_RE = re.compile(r"[一-鿿]")
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class DocChunk:
    """文档内的一个块（章节路径用于引用展示）。"""

    chunk_index: int
    chapter: str
    content: str


@dataclass
class DocIndex:
    """一份上传文件的完整索引（pickle 后整体存进缓存）。"""

    file_id: str
    session_id: str
    source_name: str
    chunks: List[DocChunk]
    #: (n_chunks, dim) float32，行已 L2 归一化 → 余弦退化为点积
    vectors: np.ndarray
    model_id: str = ""
    created_at: float = 0.0


@dataclass
class SearchHit:
    """一条检索命中。``score`` 是 RRF 融合分（**仅用于排序**，量纲无意义）；
    ``dense`` 是余弦相似度（可解释、与阈值同量纲），对外报引用时用它。"""

    chunk_index: int
    chapter: str
    content: str
    score: float
    dense: float

    def to_source(self, doc_name: str, *, snippet_chars: int = 200) -> Dict[str, Any]:
        """转 legacy ``sources`` 视图（对齐 knowledge_qa 的字段契约）。"""
        text = self.content
        return {
            "doc": doc_name or "未知文档",
            "chapter": self.chapter or "未知章节",
            "chunk_index": self.chunk_index,
            "content_snippet": text[:snippet_chars] + "..." if len(text) > snippet_chars else text,
            "score": round(self.dense, 4),
        }


@dataclass
class SearchResult:
    """一次文档内检索的结果。"""

    hits: List[SearchHit] = field(default_factory=list)
    #: 最高余弦相似度（无命中时 None）
    max_dense: Optional[float] = None
    #: 最高相关度低于 ``doc_index_min_score``——**不丢弃命中**，只用于显式告知用户
    low_confidence: bool = False
    degraded: bool = False          # 稠密打分不可用、退化为纯 BM25
    elapsed_ms: int = 0

    @property
    def empty(self) -> bool:
        return not self.hits


# ── 缓存层（后端随 file_store 配置切换；带 ping 探测与内存降级）────────

_cache = None
_cache_lock = threading.Lock()


def _build_cache():
    from core.cache import RedisBackedCache, VersionedTTLCache
    from core.config.settings import Settings

    s = Settings()
    ttl = max(1, int(getattr(s, "file_store_ttl_seconds", 3600) or 3600))
    backend = (getattr(s, "file_store_backend", "memory") or "memory").lower()

    if backend == "redis":
        url = getattr(s, "file_store_redis_url", "") or "redis://localhost:6379/0"
        try:
            import redis as _redis

            client = _redis.Redis.from_url(url, decode_responses=False)
            client.ping()   # 探测失败 → 降级内存（否则索引静默永不落盘）
            return RedisBackedCache(
                _CACHE_NAME, ttl=ttl, version=DOC_INDEX_VERSION,
                url=url, client=client,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_index Redis 后端不可用（%s），降级为进程内后端", exc)

    return VersionedTTLCache(
        _CACHE_NAME, maxsize=_MEMORY_MAXSIZE, ttl=ttl, version=DOC_INDEX_VERSION,
    )


def _get_cache():
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = _build_cache()
    return _cache


def reset_index() -> None:
    """清空索引并丢弃后端实例（测试 / 后端切换用）。

    **按当前配置现构造后端再清空**：若只在 ``_cache`` 已实例化时清，首次调用
    （后端尚未建）会静默不生效——Redis 后端的旧索引仍在且跨进程可见，
    "已重置" 的假设不成立。
    """
    global _cache
    with _cache_lock:
        try:
            _build_cache().clear()
        except Exception as exc:  # noqa: BLE001  后端不可用时不影响后续重建
            logger.warning("doc_index 清空失败: %s", exc)
        _cache = None


def _key(file_id: str, session_id: str) -> str:
    return _get_cache().key(session_id, file_id)


# ── 向量化 ────────────────────────────────────────────────────────────

def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """按行 L2 归一化（零向量保持零，不产生 NaN）。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    return (matrix / norms).astype(np.float32, copy=False)


def _embed_texts(texts: Sequence[str], settings) -> np.ndarray:
    """批量向量化（按 ``embedding_batch_size`` 分批；自建服务不宜单请求塞几百条）。"""
    from core.embeddings import get_embeddings

    emb = get_embeddings(settings)
    batch = max(1, int(getattr(settings, "embedding_batch_size", 100) or 100))
    vectors: List[List[float]] = []
    for start in range(0, len(texts), batch):
        vectors.extend(emb.embed_documents(list(texts[start:start + batch])))
    return np.asarray(vectors, dtype=np.float32)


def build(
    *,
    file_id: str,
    session_id: str,
    source_name: str,
    chunks: Sequence[DocChunk],
    settings: Optional[Any] = None,
) -> Optional[DocIndex]:
    """分块 → 向量化 → 落索引。**失败返回 None**（调用方回退全文模式）。

    索引写入在向量化之后一次性完成：半成品索引比没有索引更糟（会命中却检索不全）。
    """
    from core.config.settings import Settings

    settings = settings or Settings()
    if not getattr(settings, "doc_index_enabled", True):
        return None
    if not chunks:
        return None

    texts = [c.content for c in chunks]
    try:
        vectors = _embed_texts(texts, settings)
    except Exception as exc:  # noqa: BLE001  向量化失败 → 回退全文模式
        logger.warning("doc_index 向量化失败（回退全文模式）: file=%s err=%s", file_id[:12], exc)
        return None

    if vectors.shape[0] != len(chunks):
        logger.warning(
            "doc_index 向量条数不匹配（%d != %d），放弃建索引: file=%s",
            vectors.shape[0], len(chunks), file_id[:12],
        )
        return None

    index = DocIndex(
        file_id=file_id,
        session_id=session_id,
        source_name=source_name,
        chunks=list(chunks),
        vectors=_normalize_rows(vectors),
    )
    try:
        from core.embeddings.bge_m3 import EMBEDDING_VERSION

        index.model_id = EMBEDDING_VERSION
    except Exception:  # noqa: BLE001
        pass

    try:
        _get_cache().set(_key(file_id, session_id), index)
    except Exception as exc:  # noqa: BLE001  缓存写失败 → 索引不可用，回退全文模式
        logger.warning("doc_index 写入失败（回退全文模式）: file=%s err=%s", file_id[:12], exc)
        return None

    logger.info(
        "doc_index 已建立: file=%s name=%s chunks=%d dim=%d",
        file_id[:12], source_name, len(chunks), index.vectors.shape[1],
    )
    return index


def load(file_id: str, session_id: str) -> Optional[DocIndex]:
    """读索引；未建 / 过期 / 后端故障 → None（调用方回退全文模式）。"""
    from core.cache import MISSING

    try:
        value = _get_cache().get(_key(file_id, session_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_index 读取失败: file=%s err=%s", file_id[:12], exc)
        return None
    if value is MISSING or not isinstance(value, DocIndex):
        return None
    return value


def drop(file_id: str, session_id: str) -> None:
    """删除索引（缓存无 delete 接口 → 覆盖为哨兵值，读侧按未命中处理）。"""
    try:
        _get_cache().set(_key(file_id, session_id), None)
    except Exception:  # noqa: BLE001
        pass


# ── 检索 ─────────────────────────────────────────────────────────────

def _query_terms(question: str) -> List[str]:
    """查询词：英文/数字按词 + 中文按字符二元组（与 Milvus ngram_token_size=2 同口径）。"""
    text = (question or "").lower()
    terms = set(_WORD_RE.findall(text))
    cjk = "".join(ch for ch in text if _CJK_RE.match(ch))
    if len(cjk) == 1:
        terms.add(cjk)
    else:
        terms.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return [t for t in terms if t]


def _bm25_scores(chunks: Sequence[DocChunk], terms: Sequence[str]) -> List[float]:
    """BM25（tf/df 现算：``str.count`` / ``in`` 均为 C 层扫描，200 块约 20~40ms）。

    词长 dl 用字符长度近似——字符二元组下 token 数 ≈ 字符数，比例一致故不影响排序。
    """
    n = len(chunks)
    if not terms or not n:
        return [0.0] * n
    lengths = [max(1, len(c.content)) for c in chunks]
    avgdl = sum(lengths) / n
    scores = [0.0] * n
    for term in terms:
        df = sum(1 for c in chunks if term in c.content)
        if df == 0:
            continue
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for i, chunk in enumerate(chunks):
            tf = chunk.content.count(term)
            if not tf:
                continue
            denom = tf + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * lengths[i] / avgdl)
            scores[i] += idf * (tf * (_BM25_K1 + 1.0)) / denom
    return scores


def _ranks(scores: Sequence[float]) -> Dict[int, int]:
    """分数降序名次（从 1 开始）；零分不入榜（稀疏榜只收真实命中）。"""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {idx: pos + 1 for pos, idx in enumerate(order) if scores[idx] > 0}


def search(
    index: DocIndex,
    question: str,
    *,
    top_k: int = 6,
    min_score: float = DEFAULT_MIN_SCORE,
    settings: Optional[Any] = None,
) -> SearchResult:
    """文档内混合检索：稠密余弦 + BM25 → RRF 融合 → top-K。

    稠密打分失败（embedding 服务不可用）时**降级为纯 BM25**，检索仍可用。
    """
    import time

    t0 = time.perf_counter()
    chunks = index.chunks
    n = len(chunks)
    if n == 0:
        return SearchResult(elapsed_ms=0)

    top_k = max(1, min(int(top_k), n))

    dense: Optional[List[float]] = None
    try:
        from core.embeddings import get_embeddings

        emb = get_embeddings(settings)
        qvec = np.asarray(emb.embed_query(question), dtype=np.float32)
        norm = float(np.linalg.norm(qvec)) or 1e-12
        dense = (index.vectors @ (qvec / norm)).astype(np.float32).tolist()
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_index 查询向量化失败，退化为纯 BM25: %s", exc)

    sparse = _bm25_scores(chunks, _query_terms(question))
    dense_ranks = _ranks(dense) if dense is not None else {}
    sparse_ranks = _ranks(sparse)

    fused: Dict[int, float] = {}
    for idx, rank in dense_ranks.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + rank)
    for idx, rank in sparse_ranks.items():
        fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + rank)

    if fused:
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if len(ranked) < top_k:
            # 补齐：零信号块不参与排序但也不该缺席——上下文规模恒定（top_k 块），
            # 让"检索没抓住，但答案其实在文档里"仍有被读到的机会。
            # 无相关度的情形由 low_confidence 显式告知用户，而非靠少给上下文。
            taken = {i for i, _ in ranked}
            ranked += [(i, 0.0) for i in range(n) if i not in taken][: top_k - len(ranked)]
    else:
        # 稠密与稀疏都没给出信号 → 退回文档首部若干块（总比不给上下文强）
        ranked = [(i, 0.0) for i in range(top_k)]

    hits = [
        SearchHit(
            chunk_index=chunks[i].chunk_index,
            chapter=chunks[i].chapter,
            content=chunks[i].content,
            score=round(score, 6),
            dense=float(dense[i]) if dense is not None else 0.0,
        )
        for i, score in ranked
    ]
    max_dense = max((h.dense for h in hits), default=None) if dense is not None else None
    return SearchResult(
        hits=hits,
        max_dense=max_dense,
        low_confidence=dense is not None and (max_dense or 0.0) < float(min_score),
        degraded=dense is None,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


def search_file(
    file_id: str,
    session_id: str,
    question: str,
    *,
    top_k: Optional[int] = None,
    settings: Optional[Any] = None,
) -> Optional[SearchResult]:
    """``load`` + ``search`` 的便捷组合；未建索引返回 None（调用方回退全文模式）。

    Args:
        top_k: 覆盖 ``doc_index_top_k`` 配置（工具可按调用方要求调整）；None 用配置值。
    """
    from core.config.settings import Settings

    index = load(file_id, session_id)
    if index is None:
        return None
    settings = settings or Settings()
    if top_k is None:
        top_k = int(getattr(settings, "doc_index_top_k", 6) or 6)
    return search(
        index,
        question,
        top_k=max(1, int(top_k)),
        min_score=float(
            getattr(settings, "doc_index_min_score", DEFAULT_MIN_SCORE)
            or DEFAULT_MIN_SCORE
        ),
        settings=settings,
    )


__all__ = [
    "DocChunk",
    "DocIndex",
    "SearchHit",
    "SearchResult",
    "build",
    "load",
    "drop",
    "search",
    "search_file",
    "reset_index",
    "DOC_INDEX_VERSION",
    "DEFAULT_MIN_SCORE",
]
