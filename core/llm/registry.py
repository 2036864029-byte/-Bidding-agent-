# 功能：LLM 模型注册表 — 按槽位从 models.yaml 创建。
"""模型注册表 — models.yaml 槽位 → Provider → LangChain 模型（Phase 0 / A-5）。

改 core/config/profiles/models.yaml 即换模型，零代码改动（P0 验收 A-14）。

用法：
    from core.llm.registry import create_llm_for_slot
    router_llm = create_llm_for_slot("router")            # 复用全局 Settings
    intent_llm = create_llm_for_slot("price_intent", my_settings)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Type

import yaml
from langchain_core.language_models import BaseChatModel

from ..config.settings import Settings
from .base import LLMProvider

logger = logging.getLogger(__name__)

# models.yaml 位置：core/config/profiles/models.yaml
_MODELS_YAML = Path(__file__).resolve().parent.parent / "config" / "profiles" / "models.yaml"

# 注册表：provider 名 → Provider 类（deepseek 在模块底部自动注册）
_PROVIDERS: Dict[str, Type[LLMProvider]] = {}

# models.yaml 内容缓存（改 yaml 后调 reset_slot_cache() 生效，测试用）
_slot_config_cache: Optional[dict] = None


def register(name: str, provider_cls: Type[LLMProvider]) -> None:
    """注册一个 Provider（名称与 models.yaml 中 slot.provider 对应）。"""
    if name in _PROVIDERS:
        logger.warning("Provider %r 重复注册，覆盖", name)
    _PROVIDERS[name] = provider_cls


def available_slots() -> list[str]:
    """返回 models.yaml 中声明的全部槽位名。"""
    return list(_load_config()["slots"].keys())


def get_slot_config(slot: str) -> dict:
    """返回某槽位合并 defaults 后的完整配置（provider/model/temperature/...）。"""
    cfg = _load_config()
    slots = cfg["slots"]
    if slot not in slots:
        raise KeyError(
            f"未知模型槽位 {slot!r}，可用槽位: {list(slots.keys())}"
        )
    merged: dict = dict(cfg.get("defaults") or {})
    merged.update(slots[slot])
    return merged


def create_llm_for_slot(
    slot: str,
    settings: Optional[Settings] = None,
) -> BaseChatModel:
    """按槽位配置构造模型实例。

    Args:
        slot: models.yaml slots 下的槽位名（router / kb_generation / price_intent）。
        settings: 全局配置；None 时新建 Settings()（读根 .env）。

    Returns:
        LangChain BaseChatModel（保持 Runnable 语义，供 `|` / with_structured_output
        / bind_tools 使用——P0 不改任何调用点行为）。
    """
    settings = settings or Settings()
    slot_cfg = get_slot_config(slot)
    provider_name = slot_cfg.get("provider", "deepseek")
    try:
        provider_cls = _PROVIDERS[provider_name]
    except KeyError:
        raise KeyError(
            f"槽位 {slot!r} 指定了未注册的 provider {provider_name!r}；"
            f"已注册: {list(_PROVIDERS.keys())}"
        ) from None
    model = provider_cls(settings).build(
        model=slot_cfg.get("model"),
        temperature=slot_cfg.get("temperature"),
    )
    logger.info(
        "模型槽位 %r → provider=%s model=%s temperature=%s",
        slot, provider_name,
        getattr(model, "model_name", getattr(model, "model", "?")),
        slot_cfg.get("temperature"),
    )
    return model


def reset_slot_cache() -> None:
    """清空 models.yaml 缓存（测试用：模拟运行时改 yaml 生效）。"""
    global _slot_config_cache
    _slot_config_cache = None


def _load_config() -> dict:
    global _slot_config_cache
    if _slot_config_cache is None:
        if not _MODELS_YAML.exists():
            raise FileNotFoundError(f"models.yaml 不存在: {_MODELS_YAML}")
        with open(_MODELS_YAML, encoding="utf-8") as f:
            _slot_config_cache = yaml.safe_load(f)
    return _slot_config_cache


# 自动注册内置 Provider
from .deepseek import DeepSeekProvider  # noqa: E402

register(DeepSeekProvider.provider_name, DeepSeekProvider)
