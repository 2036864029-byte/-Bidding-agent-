# 功能：调用 SiliconFlow Reranker 重排序，并提供故障重试和降级契约。
"""Reranker protocol and SiliconFlow HTTP client.

W0-3：状态按调用返回，不落实例属性。旧实现把 ``last_status`` / ``retry_count``
写在实例上，而 reranker 随检索单例进程级共享 → 并发请求互相覆盖状态，可能静默
走错降级分支（审计 A3）。现在 ``rerank()`` 返回 ``(results, status)``。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Protocol, Tuple

import requests

from core.contracts import RerankerStatus
from core.net import ensure_no_proxy_for_loopback, is_loopback_url

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    """Protocol implemented by reranker clients used by retrieval."""

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 3,
    ) -> Tuple[List[Dict[str, Any]], RerankerStatus]:
        """Return ``(results, status)``.

        ``results`` 为按 relevance 降序的 ``[{"index": int, "relevance_score": float}, ...]``；
        ``status`` 描述本次调用的结果（NOT_REQUESTED / SUCCESS / FAILED），
        与调用方共享的实例状态无关。
        """
        ...


class SiliconFlowReranker:
    """SiliconFlow Reranker API client for the OpenAI-compatible /rerank endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        http_client: Any = requests,
        *,
        timeout: float = 30,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else "https://api.siliconflow.cn/v1"
        # 环回端点（本机隧道）绕开环境代理，否则 requests 会把 127.0.0.1 也发给代理
        # → 连接必然失败，且 curl 手测是通的，极易误判（见 core/net.py）
        if is_loopback_url(self._base_url) and ensure_no_proxy_for_loopback():
            logger.info("Reranker 端点为环回地址，已加入 NO_PROXY（避免被系统代理劫持）")
        self._http_client = http_client
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep_fn = sleep_fn

    def rerank(
        self, query: str, documents: List[str], top_k: int = 3,
    ) -> Tuple[List[Dict[str, Any]], RerankerStatus]:
        """Call SiliconFlow to rerank candidate documents.

        Args:
            query: User question.
            documents: Candidate document texts.
            top_k: Maximum number of results to return.

        Returns:
            ``(results, status)``；``results`` 为降序的
            ``[{"index": int, "relevance_score": float}, ...]``。
        """
        if not documents:
            return [], RerankerStatus.NOT_REQUESTED

        max_attempts = self._max_retries + 1
        for attempt in range(max_attempts):
            try:
                resp = self._http_client.post(
                    f"{self._base_url}/rerank",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_k, len(documents)),
                    },
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                normalized = sorted(
                    results,
                    key=lambda item: item.get("relevance_score", 0),
                    reverse=True,
                )
                return normalized, RerankerStatus.SUCCESS
            except Exception as exc:
                if attempt >= self._max_retries or not self._is_retryable(exc):
                    logger.warning(
                        "Reranker API 调用失败: %s，保留 RRF 原始排序",
                        exc,
                    )
                    return [], RerankerStatus.FAILED

                delay = self._retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Reranker API 瞬时失败 (%s)，%.2fs 后重试 %d/%d",
                    exc,
                    delay,
                    attempt + 1,
                    self._max_retries,
                )
                self._sleep_fn(delay)

        return [], RerankerStatus.FAILED

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, requests.exceptions.RequestException):
            return status_code is None
        return status_code == 429 or (
            isinstance(status_code, int) and 500 <= status_code <= 599
        )
