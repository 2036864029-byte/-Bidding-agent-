"""channels.feishu.backend — 后端 HTTP 客户端（调本地 /chat）。

通道是独立进程，按执行计划 D1 **通过 HTTP 调 127.0.0.1:8000/chat**（非流式），
复用已有进程里的 AgentGraph 单例、限流、审计、trace、错误信封——不进程内再起一张图。

请求头：
- ``X-Tenant-ID: feishu:<open_id>``（按人限流/隔离，见 session.py）
- ``X-API-Key``（后端 API_KEYS 非空时才需要，勿把 /chat 加进 auth_exempt_paths）
- ``X-Request-ID``（透传飞书 event_id，便于与后端日志/trace 串联）

环回地址用 ``trust_env=False`` 直连：绕开系统代理（httpx 默认把 HTTP_PROXY
套到环回地址上会连不上，见 core/net.py 背景，2026-09-14 实测踩坑）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """后端不可用（连接失败 / 非 2xx / 响应非 JSON），与"业务上失败"区分开。"""


class BackendClient:
    """调 POST /chat 的薄封装（http_client 可注入，测试用 MockTransport 不真连）。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: str = "",
        timeout: float = 120.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._base = (base_url or "").rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._http = http_client if http_client is not None else httpx.Client(
            timeout=timeout, trust_env=False
        )

    def chat(
        self,
        question: str,
        *,
        session_id: str,
        tenant_id: str,
        request_id: str = "",
    ) -> Dict[str, Any]:
        """调 POST /chat（stream=false），返回 ChatResponse 响应 dict。

        Raises:
            BackendError: 后端连不上 / 非 2xx / 响应非 JSON（调用方回友好提示）。
        """
        headers = {"X-Tenant-ID": tenant_id}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            resp = self._http.post(
                f"{self._base}/chat",
                headers=headers,
                json={
                    "question": question,
                    "session_id": session_id,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("后端返回非 2xx: %s %s", exc.response.status_code, exc.response.url)
            raise BackendError(f"后端 HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            logger.warning("后端连接失败: %s", exc)
            raise BackendError(str(exc)) from exc
        except ValueError as exc:
            logger.warning("后端响应非 JSON: %s", exc)
            raise BackendError("后端响应非 JSON") from exc
        if not isinstance(data, dict):
            raise BackendError(f"后端响应形状异常: {type(data).__name__}")
        return data


__all__ = ["BackendClient", "BackendError"]
