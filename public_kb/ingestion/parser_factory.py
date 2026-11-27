# 功能：根据 Settings 选择 PDF 解析器（本地 CLI / 官方 API / 自建服务 / 三档路由）。
"""PDF 解析器工厂（L6 接线点 + 解析后端热切换）。

选择顺序：

1. `pdf_tiered_routing_enabled=true` → `PdfRouter`（三档路由）。复杂页由所选
   后端解析：`official` 走官方 API、`selfhosted` 走自建 /parse 服务、`local`
   维持原有自建服务默认（PdfRouter 内部自建，行为不变）。
2. 否则按 `pdf_parse_backend` 直出**全量**解析器：
   - `local`      → `MinerUParser`（magic-pdf CLI）——**默认**，本地开发零变化
   - `official`   → `MinerUCloudParser`（mineru.net 官方 API）——云服务器用
   - `selfhosted` → `MinerUApiParser`（自建 /parse 服务）

非法值一律回退 `local`：宁可本地解析失败，也不要静默把文档传到付费云 API。

只依赖 Services + Transforms 子树，不引入 langchain_core，避免 RAG
引擎核心依赖污染 PDF 解析路径。
"""
from __future__ import annotations

import logging

from core.config.settings import Settings

from ..services.mineru_api_parser import MinerUApiParser
from ..services.mineru_cloud_parser import MinerUCloudParser
from ..services.mineru_parser import MinerUParser
from .normalizers.pdf.pdf_router import PdfRouter

logger = logging.getLogger(__name__)

# 支持的后端取值（与 core/config/settings.py 的注释保持一致）
_BACKENDS = {"local", "official", "selfhosted"}


def resolve_backend(settings: Settings) -> str:
    """读取并校验 `pdf_parse_backend`，非法值回退 local。"""
    raw = (getattr(settings, "pdf_parse_backend", "") or "").strip().lower()
    if raw in _BACKENDS:
        return raw
    if raw:
        logger.warning("未知的 pdf_parse_backend=%r，按 local 处理", raw)
    return "local"


def build_remote_parser(settings: Settings):
    """构造「远程」解析器（official / selfhosted）。

    Returns:
        MinerUCloudParser | MinerUApiParser | None —— local 后端返回 None，
        由调用方自行使用本地 CLI（MinerUParser）。
    """
    backend = resolve_backend(settings)
    if backend == "official":
        return MinerUCloudParser(settings)
    if backend == "selfhosted":
        return MinerUApiParser(settings)
    return None


def build_pdf_parser(
    settings: Settings,
) -> MinerUParser | PdfRouter | MinerUApiParser | MinerUCloudParser:
    """根据 Settings 选择 PDF 解析器。"""
    backend = resolve_backend(settings)

    if settings.pdf_tiered_routing_enabled:
        logger.info("PDF 三档路由已启用（Tier C 后端=%s）", backend)
        remote = build_remote_parser(settings)
        # PdfRouter / MinerURouter 对 parser 是 duck-typed（测试也注入 fake），
        # 注解写的是 MinerUApiParser；official 后端传入同契约的 MinerUCloudParser。
        return PdfRouter(settings, miner_u_parser=remote)  # type: ignore[arg-type]

    if backend == "official":
        logger.info("PDF 解析后端=official（MinerU 官方 API）")
        return MinerUCloudParser(settings)
    if backend == "selfhosted":
        logger.info("PDF 解析后端=selfhosted（自建 /parse 服务）")
        return MinerUApiParser(settings)

    logger.info("PDF 解析后端=local（magic-pdf CLI，M1 行为不变）")
    return MinerUParser(settings)
