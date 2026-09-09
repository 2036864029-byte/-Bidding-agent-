"""core.llm.registry — 槽位配置 / 注册 / 按槽位创建模型（方案 Phase 0 / A-13）。"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import pytest

import core.llm.registry as registry
from core.config.settings import Settings
from core.llm.base import LLMProvider

warnings.filterwarnings("ignore", category=DeprecationWarning)


@dataclass
class FakeLLM:
    """伪造模型对象：记录 build 收到的覆盖参数，不触碰任何网络。"""

    model_name: str
    temperature: float


class FakeProvider(LLMProvider):
    provider_name = "fake"

    def __init__(self, settings) -> None:
        super().__init__(settings)

    def build(self, *, model=None, temperature=None, callbacks=None):
        return FakeLLM(
            model_name=model or self._settings.llm_model,
            temperature=(
                self._settings.llm_temperature if temperature is None else temperature
            ),
        )


# 注入合成槽位配置（模拟"改 models.yaml 即换模型"）：
# 用 monkeypatch.setattr 临时替换缓存，结束自动还原。
_SYNTH_CONFIG = {
    "defaults": {"provider": "fake", "model": None, "temperature": 0.0},
    "slots": {
        "router": {"provider": "fake", "model": "fake-model-router", "temperature": 0.2},
        "kb_generation": {"provider": "fake", "model": None, "temperature": 0.7},
    },
}


def _install_synth_config(monkeypatch):
    registry.reset_slot_cache()
    monkeypatch.setattr(registry, "_slot_config_cache", _SYNTH_CONFIG)
    monkeypatch.setattr(registry, "_PROVIDERS", {"fake": FakeProvider})


def test_slots_declared_match_plan_call_points():
    """P0 三个实际 LLM 调用点 + P6 Step3 doc_agent + W3-3 deep_agent 槽位；general_chat 不调 LLM 故无槽位。"""
    assert registry.available_slots() == ["router", "kb_generation", "price_intent", "doc_agent", "deep_agent", "rewrite"]


def test_get_slot_config_merges_defaults():
    cfg = registry.get_slot_config("price_intent")
    assert cfg["provider"] == "deepseek"
    assert cfg["model"] is None  # 继承 settings.llm_model（env LLM_MODEL 可覆盖）
    assert cfg["temperature"] == 0.0


def test_unknown_slot_raises():
    with pytest.raises(KeyError):
        registry.get_slot_config("not_a_slot")


def test_unknown_provider_raises(monkeypatch):
    registry.reset_slot_cache()
    synth = {
        "defaults": {"provider": "missing-provider"},
        "slots": {"router": {}},
    }
    monkeypatch.setattr(registry, "_slot_config_cache", synth)
    with pytest.raises(KeyError, match="missing-provider"):
        registry.create_llm_for_slot("router", Settings())


def test_create_honors_slot_model_and_temperature(monkeypatch):
    """改 models.yaml 槽位 → 零代码改动生效（A-14 验收的自动化等价）。"""
    _install_synth_config(monkeypatch)
    model = registry.create_llm_for_slot("router", Settings())
    assert model.model_name == "fake-model-router"
    assert model.temperature == 0.2

    # model: null 的槽位 → 继承 settings.llm_model
    model2 = registry.create_llm_for_slot("kb_generation", Settings())
    assert model2.model_name == Settings().llm_model
    assert model2.temperature == 0.7


def test_register_overwrites_with_warning(monkeypatch):
    monkeypatch.setattr(registry, "_PROVIDERS", {"fake": FakeProvider})
    registry.register("fake", FakeProvider)  # 重复注册 → 仅告警不报错
    assert registry._PROVIDERS["fake"] is FakeProvider


def test_deepseek_registered_by_default():
    assert "deepseek" in registry._PROVIDERS
