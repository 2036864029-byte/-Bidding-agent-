"""项目概况相似检索与金额参考 —— POST /inquiry/reference 的 HTTP 查询层。

本文件是**薄转发层**：查询核心（向量检索 + bidding 回表 + 万元打包）在
`price_engine/reference.py`（两个入口共用——本端点 + 聊天里的 product_demand 采购需求）。
保持 `search_reference` 导出名不变，`api/server.py` 与既有测试零感知。

独立新链路（与 price_engine / 公共检索完全并列，纯增量）：用户粘贴一段招标项目
概况 → BGE-M3 向量化 → Milvus 集合 `project_announcement_semantic` 相似检索 →
MySQL `bidding` 库回表（主信息 + 产品明细）→ 金额统一万元 → 询价页右栏参考。

金额口径（方案 §7）：`winning_amount_wan` / `amount_stats.*_wan` 一律**万元**；
产品 `unit_price_yuan` 保留**元**（字段名即单位）。返回 snake_case 原始形状。
"""

from __future__ import annotations

from core.vectorstore.namespace import PROJECT_ANNOUNCEMENT_COLLECTION  # noqa: F401
from price_engine.reference import (  # noqa: F401  # 薄转发：核心在引擎层
    DEFAULT_MIN_AMOUNT,
    DEFAULT_TOP_K,
    MIN_TEXT_CHARS,
    SCORE_THRESHOLD,
    _amount_stats,
    _collection_ready,
    _fetch_announcements,
    _fetch_products,
    _milvus_client,
    _parse_price,
    _retrieve,
    search_reference,
)

__all__ = [
    "search_reference",
    "MIN_TEXT_CHARS",
    "DEFAULT_TOP_K",
    "DEFAULT_MIN_AMOUNT",
    "SCORE_THRESHOLD",
]
