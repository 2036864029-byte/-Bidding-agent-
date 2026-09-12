"""NormalizedDocument IR + 文档级哈希/SimHash（方案 Phase 5 / E-1）。

三段式流水线的中间表示（IR）：source → NormalizedDocument → clean/chunk → sink。
`ir_format` 区分 markdown（向量路）与 table（结构路，供 price 侧 SQL 消费）。

纯 dataclass + 纯函数，无第三方依赖。chunk 契约仍由 contracts.py 定义，本模块
只到"文档"粒度，不越界。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal

SourceFormat = Literal["text", "csv", "pdf", "image"]
IrFormat = Literal["markdown", "table"]

# 统一换行（与 chunk_ids.normalize_chunk_text 口径一致，保证哈希稳定）
_NEWLINE_RE = __import__("re").compile(r"\r\n|\r")


@dataclass(frozen=True)
class NormalizedDocument:
    """单个来源文档的规范化中间表示。"""

    doc_id: str
    source_format: SourceFormat
    ir_format: IrFormat
    content: str
    source_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)  # parser_version / quality_score / ...


def compute_source_hash(path: str | Path) -> str:
    """文件字节 MD5（文档级去重/缓存 key 口径，与 pdf_mineru_router 一致）。

    Args:
        path: 源文件路径。

    Returns:
        MD5 十六进制 32 位。
    """
    data = Path(path).read_bytes()
    return hashlib.md5(data).hexdigest()


def simhash(text: str, shingle_size: int = 4, bits: int = 64) -> int:
    """文本 SimHash（近似去重：捕获轻微改动的重复文档）。

    连续字符 shingle + MD5 加权投票。仅用于文档级判重（E-3 ledger），
    不作为精确判据——精确判据始终是 ``compute_source_hash`` 或 chunk_uid。
    """
    if not text:
        return 0
    norm = _NEWLINE_RE.sub("\n", text).strip()
    if not norm:
        return 0

    # 去掉空白差异后再取 shingle，降低排版噪声
    compact = "".join(norm.split())

    weights: dict[int, int] = {}
    n = len(compact)
    for i in range(max(1, n - shingle_size + 1)):
        shingle = compact[i:i + shingle_size]
        h = int(hashlib.md5(shingle.encode("utf-8")).hexdigest()[:16], 16)
        weights[h] = weights.get(h, 0) + 1

    acc = [0] * bits
    for h, w in weights.items():
        for b in range(bits):
            acc[b] += w if (h >> b) & 1 else -w
    out = 0
    for b in range(bits):
        if acc[b] > 0:
            out |= 1 << b
    return out


def hamming_distance(a: int, b: int) -> int:
    """两个 SimHash 的海明距离（越小越相似）。"""
    return bin(a ^ b).count("1")
