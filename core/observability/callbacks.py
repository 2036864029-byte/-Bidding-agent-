"""LLM 调用埋点回调（W2-2）— 耗时 / token / 失败。

挂载点：`core.llm.registry.create_llm_for_slot` 构造模型时注入（带槽位名），
因此所有经槽位创建的 LLM（router / kb_generation / price_intent / doc_agent）
自动被埋点，调用点零改动。

注意（已知噪声）：`agent/router.py` 构建时会用 `with_structured_output` 做一次
能力探测；DeepSeek 当前不支持该 response_format，探测固定返回 400 并触发
tool-calling 回退（属预期分支）。因此 `llm_errors_total{slot="router"}` 每进程
会有 1 次，不代表真实故障（W0-4 已把 router 收敛为每进程构建一次）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Tuple
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from .metrics import record_llm_error, record_llm_latency, record_llm_tokens

logger = logging.getLogger(__name__)


def usage_from_response(response: Any) -> Tuple[int, int]:
    """从 LLMResult 提取 (prompt_tokens, completion_tokens)。

    优先 `llm_output.token_usage`（OpenAI 兼容），回退
    `generations[].message.usage_metadata`（langchain-core 统一口径）。
    """
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if prompt or completion:
        return prompt, completion

    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
            if meta:
                return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    return 0, 0


class LLMMetricsCallback(BaseCallbackHandler):
    """记录每次 LLM 调用的耗时与 token 消耗（并输出 OTel span，E0-3）。"""

    def __init__(self, slot: str, model: str = "") -> None:
        self._slot = slot
        self._model = model
        self._starts: Dict[UUID, float] = {}
        self._start_ns: Dict[UUID, int] = {}

    def _start(self, run_id: Any) -> None:
        if run_id is not None:
            self._starts[run_id] = time.perf_counter()
            self._start_ns[run_id] = time.time_ns()

    def _emit_span(
        self,
        start_ns: Any,
        *,
        latency: Any = None,
        prompt: int = 0,
        completion: int = 0,
        error: Any = None,
    ) -> None:
        """输出 LLM span（gen_ai 语义约定）；追踪未启用时为零开销 no-op。"""
        try:
            from .tracing import get_tracer

            attrs = {
                "gen_ai.system": "deepseek",
                "gen_ai.request.model": self._model,
                "gen_ai.usage.input_tokens": prompt,
                "gen_ai.usage.output_tokens": completion,
                "ztb.slot": self._slot,
            }
            if latency is not None:
                attrs["ztb.latency_ms"] = round(latency * 1000, 2)
            if error is not None:
                attrs["ztb.error"] = str(error)[:200]
            sp = get_tracer().start_span("llm.call", attributes=attrs, start_time=start_ns)
            sp.end()
        except Exception:  # 埋点失败绝不影响主流程
            pass

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs) -> None:
        self._start(run_id)

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs) -> None:
        self._start(run_id)

    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        start = self._starts.pop(run_id, None)
        start_ns = self._start_ns.pop(run_id, None)
        latency = time.perf_counter() - start if start is not None else None
        if latency is not None:
            record_llm_latency(self._slot, self._model, latency)
        prompt, completion = usage_from_response(response)
        if prompt or completion:
            record_llm_tokens(self._slot, self._model, prompt=prompt, completion=completion)
        self._emit_span(start_ns, latency=latency, prompt=prompt, completion=completion)

    def on_llm_error(self, error, *, run_id=None, **kwargs) -> None:
        self._starts.pop(run_id, None)
        start_ns = self._start_ns.pop(run_id, None)
        record_llm_error(self._slot, self._model)
        self._emit_span(start_ns, error=error)


__all__ = ["LLMMetricsCallback", "usage_from_response"]
