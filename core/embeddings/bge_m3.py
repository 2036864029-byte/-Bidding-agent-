# 功能：创建和管理 BGE-M3 等 embedding 向量化服务。
"""BGE-M3 Embedding 服务 — 由 public_kb/services/embeddings.py 上提迁移。

支持 OpenAI / SiliconFlow / 任意兼容 OpenAI API 格式的嵌入服务。
通过 Settings.embedding_api_key / embedding_base_url 切换后端（env 驱动）。

安全机制：
  - 自动截断超过 settings.chunk_max_chars 字符的文本，防止 bge 系列模型
    对超长文本返回 400 错误（与 config.chunk_max_chars 单源对齐，勿两处维护）。
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_openai import OpenAIEmbeddings

from ..config.settings import Settings
from ..net import ensure_no_proxy_for_loopback, is_loopback_url, loopback_direct_http_client

logger = logging.getLogger(__name__)

# ── 版本元数据（供 schema_registry 校验：换模型/维度需重建集合）──
EMBEDDING_MODEL_ID = "BAAI/bge-m3"
EMBEDDING_VERSION = "bge-m3-v1"  # 模型 → 向量空间约定；升级模型版本号须递增


class _SafeEmbeddings(OpenAIEmbeddings):
    """OpenAIEmbeddings 子类，对超长文本自动截断，防止 400 错误。"""

    def __init__(self, *args: object, max_text_chars: int = 2000, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._max_text_chars = max_text_chars

    def embed_documents(
        self,
        texts: list[str],
        chunk_size: Optional[int] = None,
        **kwargs: object,
    ) -> list[list[float]]:
        limit = self._max_text_chars
        safe_texts = [
            t[:limit] if len(t) > limit else t for t in texts
        ]
        truncated = sum(1 for t in texts if len(t) > limit)
        if truncated:
            logger.debug(
                "Embedding 安全截断: %d 条文本超出 %d 字符限制",
                truncated, limit,
            )
        return super().embed_documents(safe_texts, chunk_size=chunk_size, **kwargs)

    def embed_query(self, text: str) -> list[float]:
        limit = self._max_text_chars
        safe = text[:limit] if len(text) > limit else text
        return super().embed_query(safe)


def create_embeddings(settings: Settings) -> OpenAIEmbeddings:
    """根据配置创建 OpenAIEmbeddings 实例。

    兼容 OpenAI 原生 API 及第三方兼容服务（如 SiliconFlow、CloseAI）。
    通过设置 EMBEDDING_BASE_URL 环境变量即可切换后端。

    返回的实例内置超长文本保护，不会因单条文本超出模型 token 限制而报错。

    Args:
        settings: 全局配置。

    Returns:
        配置好的 OpenAIEmbeddings 实例（实际为 _SafeEmbeddings）。
    """
    kwargs: dict = {
        "model": settings.embedding_model,
        "api_key": settings.embedding_api_key,
        # 关闭 token 长度检查，否则 langchain 会先把文本转为 token ID 再发送
        # SiliconFlow 等第三方服务不接受 token ID 格式，只接受原始文本
        "check_embedding_ctx_length": False,
        # 标准化超时与重试配置
        "timeout": settings.embedding_timeout,
        "max_retries": settings.embedding_max_retries,
    }

    # 若有自定义 base_url 则传入（如 SiliconFlow / CloseAI）
    if settings.embedding_base_url:
        kwargs["base_url"] = settings.embedding_base_url
        if is_loopback_url(settings.embedding_base_url):
            # 环回端点（本机隧道）必须绕开环境代理：代理够不到客户端的 localhost，
            # 否则表现为 APIConnectionError 而 curl 手测却是通的（见 core/net.py）。
            ensure_no_proxy_for_loopback()
            client = loopback_direct_http_client(timeout=float(settings.embedding_timeout))
            if client is not None:
                # 显式 mount 才是在长驻进程里真正可靠的那条路
                kwargs["http_client"] = client
            logger.info("Embedding 端点为环回地址：已注入直连 httpx client + NO_PROXY")

    logger.info(
        "初始化 Embedding: model=%s, base_url=%s, timeout=%ds, max_retries=%d",
        settings.embedding_model,
        settings.embedding_base_url or "OpenAI 默认",
        settings.embedding_timeout,
        settings.embedding_max_retries,
    )

    # 与切片参数单源对齐（任务 M5 Step A）：bge 系列模型的文本截断上限
    return _SafeEmbeddings(max_text_chars=settings.chunk_max_chars, **kwargs)
