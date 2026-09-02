# 功能：DeepSeek（OpenAI 兼容）LLM Provider。
"""DeepSeekProvider — 由 public_kb/services/llm.py 上提迁移（Phase 0 / A-4）。

原 ``create_llm``（services/llm.py:19-39）的 ChatOpenAI 构造逻辑包进
``DeepSeekProvider.build()``；模块级 ``create_llm(settings, *, temperature=None)``
保留兼容签名——public_kb/services/llm.py 及旧调用方经 re-export 继续可用。
"""

from __future__ import annotations

from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..config.settings import Settings
from .base import LLMProvider


class DeepSeekProvider(LLMProvider):
    """DeepSeek / 任意 OpenAI 兼容 Chat 模型。"""

    provider_name = "deepseek"

    def build(
        self,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatOpenAI:
        settings = self._settings
        kwargs: dict = {
            "model": model or settings.llm_model,
            "api_key": settings.llm_api_key,
            # None → 沿用 settings.llm_temperature；否则显式覆盖
            # （路由/意图解析等需要确定性输出的场景传 0.0）
            "temperature": (
                settings.llm_temperature if temperature is None else temperature
            ),
            "timeout": settings.llm_timeout,
            "max_retries": settings.llm_max_retries,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        return ChatOpenAI(**kwargs)


def create_llm(
    settings: Settings, *, temperature: Optional[float] = None
) -> BaseChatModel:
    """兼容入口：按 Settings 构造 ChatOpenAI（等价原 services.llm.create_llm）。"""
    return DeepSeekProvider(settings).build(temperature=temperature)
