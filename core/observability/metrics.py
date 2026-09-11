# 功能：业务指标（进程内累计 + Prometheus 导出）— W2-2 扩展。
"""metrics — 分支耗时 / 护栏失败 / 召回降级 / LLM 调用。

双轨设计：
  - 进程内累计（dict）保留 `snapshot()`，供测试与诊断脚本断言；
  - 同步写入 prometheus_client 的 Counter/Histogram，经 `render_metrics()` 暴露
    `/metrics`（W2-2）。

埋点位置：
  - `core.errors.fallback.with_fallback`（分支耗时）
  - knowledge_qa 子图护栏分支（校验失败计数）
  - `HybridRetriever._dense_fallback`（召回降级层）
  - `core.observability.callbacks.LLMMetricsCallback`（LLM 耗时 / token / 失败）
"""

from __future__ import annotations

import logging
from typing import Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# ── 进程内累计（snapshot 用）──────────────────────────────────
_BRANCH_LATENCY: dict[str, list[int]] = {}
_GUARDRAIL_FAILS: int = 0
_RECALL_FALLBACKS: dict[str, int] = {}

# ── Prometheus 导出 ──────────────────────────────────────────
_BRANCH_LATENCY_HIST = Histogram(
    "agent_branch_latency_seconds", "分支/节点执行耗时（秒）", ["branch"],
)
_GUARDRAIL_FAILS_TOTAL = Counter(
    "agent_guardrail_fails_total", "护栏校验失败次数", ["branch"],
)
_RECALL_FALLBACK_TOTAL = Counter(
    "agent_recall_fallback_total", "检索降级次数", ["layer"],
)
_LLM_LATENCY_HIST = Histogram(
    "llm_latency_seconds", "LLM 调用耗时（秒）", ["slot", "model"],
)
_LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total", "LLM token 消耗", ["slot", "model", "kind"],
)
_LLM_ERRORS_TOTAL = Counter(
    "llm_errors_total", "LLM 调用失败次数", ["slot", "model"],
)
_CACHE_ACCESS_TOTAL = Counter(
    "retrieval_cache_access_total", "检索缓存访问次数", ["cache", "result"],
)


def record_cache_access(cache: str, *, hit: bool) -> None:
    """检索缓存命中/未命中计数（W4-2）。"""
    _CACHE_ACCESS_TOTAL.labels(cache=cache or "unknown", result="hit" if hit else "miss").inc()


def record_branch_latency(branch: str, latency_ms: float) -> None:
    """分支耗时（毫秒）。"""
    _BRANCH_LATENCY.setdefault(branch, []).append(int(latency_ms))
    _BRANCH_LATENCY_HIST.labels(branch=branch or "unknown").observe(max(latency_ms, 0) / 1000.0)
    logger.info("metric branch_latency branch=%s latency_ms=%d", branch, int(latency_ms))


def record_guardrail_fail(branch: Optional[str] = None) -> None:
    """护栏校验失败计数（回答返回但引用未全过）。"""
    global _GUARDRAIL_FAILS
    _GUARDRAIL_FAILS += 1
    _GUARDRAIL_FAILS_TOTAL.labels(branch=branch or "unknown").inc()
    logger.warning("metric guardrail_fail branch=%s", branch or "unknown")


def record_recall_fallback(layer: str) -> None:
    """召回降级命中层（dense_only / fulltext_missing / ...）。"""
    _RECALL_FALLBACKS[layer] = _RECALL_FALLBACKS.get(layer, 0) + 1
    _RECALL_FALLBACK_TOTAL.labels(layer=layer or "unknown").inc()
    logger.warning("metric recall_fallback layer=%s", layer)


def record_llm_latency(slot: str, model: str, latency_s: float) -> None:
    """LLM 调用耗时（秒）。"""
    _LLM_LATENCY_HIST.labels(slot=slot, model=model or "?").observe(max(latency_s, 0.0))
    logger.info("metric llm_latency slot=%s model=%s latency_ms=%d",
                slot, model or "?", int(latency_s * 1000))


def record_llm_tokens(
    slot: str, model: str, *, prompt: int = 0, completion: int = 0,
) -> None:
    """LLM token 消耗（prompt / completion）。"""
    label_model = model or "?"
    if prompt:
        _LLM_TOKENS_TOTAL.labels(slot=slot, model=label_model, kind="prompt").inc(prompt)
    if completion:
        _LLM_TOKENS_TOTAL.labels(slot=slot, model=label_model, kind="completion").inc(completion)
    logger.info("metric llm_tokens slot=%s model=%s prompt=%d completion=%d",
                slot, label_model, prompt, completion)


def record_llm_error(slot: str, model: str) -> None:
    """LLM 调用失败。"""
    _LLM_ERRORS_TOTAL.labels(slot=slot, model=model or "?").inc()
    logger.warning("metric llm_error slot=%s model=%s", slot, model or "?")


def render_metrics() -> bytes:
    """Prometheus 文本格式快照（供 /metrics 端点）。"""
    return generate_latest()


def content_type() -> str:
    """Prometheus 响应 Content-Type。"""
    return CONTENT_TYPE_LATEST


def snapshot() -> dict:
    """进程内指标快照（测试/诊断用）。"""
    return {
        "branch_latency_ms": {k: v for k, v in _BRANCH_LATENCY.items()},
        "guardrail_fails": _GUARDRAIL_FAILS,
        "recall_fallbacks": dict(_RECALL_FALLBACKS),
    }


def reset() -> None:
    """清空累计（测试用；Prometheus 计数器不可重置，仅清进程内快照）。"""
    _BRANCH_LATENCY.clear()
    global _GUARDRAIL_FAILS
    _GUARDRAIL_FAILS = 0
    _RECALL_FALLBACKS.clear()


__all__ = [
    "content_type",
    "record_branch_latency",
    "record_cache_access",
    "record_guardrail_fail",
    "record_llm_error",
    "record_llm_latency",
    "record_llm_tokens",
    "record_recall_fallback",
    "render_metrics",
    "reset",
    "snapshot",
]
