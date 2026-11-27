"""api.feishu_oauth — 飞书 OAuth 绑定端点（并行线 A）。

三个端点：
- ``GET /feishu/auth_url?session_id=&redirect=`` → {auth_url, state}
- ``GET /feishu/oauth/callback?code=&state=``  → 302 回前端（?feishu=bound）
- ``GET /feishu/binding?session_id=``          → {bound, open_id_mask}

state 采用 **HMAC(session_id, app_secret)** 的无状态方案：auth_url 把签名
随跳转链带出，回调解析并校验签名（防伪造/CSRF），不落存储。session_id 泄露
的残余风险与系统「匿名 session 无认证」的既有边界一致（见执行计划 A-R3 与 §4A.1）。

换 token 的 HTTP 调用是可替换接缝（``_exchange_token``），测试 monkeypatch，
不真连飞书。无 ``FEISHU_APP_ID`` 时 auth_url / callback 返回 503。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from core.config.settings import Settings
from core.feishu_binding import bind, is_bound, is_valid_open_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feishu", tags=["feishu"])

_SESSION_SEP = "."
_STATE_SIG_BYTES = 16


def _callback_target(s: Settings, redirect: str) -> str:
    """回调后的浏览器回跳目标。

    飞书回调只带 code+state，**不会回传前端传的 redirect 参数** —— FastAPI 会把它
    当默认值 ``"/"``。故 ``""`` 与 ``"/"`` 都视为「未指定」，回落
    ``FEISHU_FRONTEND_URL``（配置的前端地址）。显式 redirect 只放行两类：
      - 相对路径（``/chat`` 这类；拒绝 ``//`` 与 ``/\\`` 开头的协议相对变体）
      - 与 ``FEISHU_FRONTEND_URL`` **同源**的绝对地址
    其余（跨源 http/https、javascript:、data: 等）一律回落默认目标 ——
    否则 ``redirect=http://evil.example`` 就变成 open redirect。
    """
    r = (redirect or "").strip()
    base = (s.feishu_frontend_url or "").strip().rstrip("/")
    if r not in ("", "/"):
        if r.startswith("/") and r[1:2] not in ("/", "\\"):
            return r
        if base and (r == base or r.startswith(base + "/")):
            return r
    return base or "/"


def _make_state(session_id: str, app_secret: str) -> str:
    sig = hmac.new(
        app_secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:_STATE_SIG_BYTES]
    return f"{session_id}{_SESSION_SEP}{sig}"


def _parse_state(state: str, app_secret: str) -> Optional[str]:
    """解析并校验 state → session_id；伪造/格式错返回 None。"""
    if not state or not app_secret:
        return None
    if _SESSION_SEP not in state:
        return None
    session_id, sig = state.rsplit(_SESSION_SEP, 1)
    expect = hmac.new(
        app_secret.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:_STATE_SIG_BYTES]
    return session_id if hmac.compare_digest(sig, expect) else None


def _exchange_token(code: str, s: Settings) -> Optional[str]:
    """授权码 → open_id（**可替换接缝**，测试注入假件，不真连飞书）。"""
    import httpx

    resp = httpx.post(
        f"{s.feishu_domain.rstrip('/')}/open-apis/authen/v1/oidc/access_token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": s.feishu_app_id,
            "client_secret": s.feishu_app_secret,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        logger.warning("飞书 OAuth 换 token 失败: code=%s msg=%s",
                       data.get("code"), data.get("msg"))
        return None
    open_id = (data.get("data") or {}).get("open_id") or data.get("open_id")
    if open_id and is_valid_open_id(open_id):
        return open_id
    return None


@router.get("/auth_url")
def feishu_auth_url(
    session_id: str, redirect: str = "/"
) -> dict:
    """生成飞书 OAuth 授权跳转链（带 state）；前端「连接飞书」按钮跳这里。"""
    s = Settings()
    if not (s.feishu_app_id and s.feishu_app_secret and s.feishu_redirect_uri):
        raise HTTPException(
            status_code=503,
            detail="飞书未配置（缺 FEISHU_APP_ID/SECRET/REDIRECT_URI）",
        )
    state = _make_state(session_id, s.feishu_app_secret)
    auth_url = (
        f"{s.feishu_domain.rstrip('/')}/open-apis/authen/v1/authorize"
        f"?client_id={quote(s.feishu_app_id)}&response_type=code"
        f"&redirect_uri={quote(s.feishu_redirect_uri, safe='')}"
        f"&scope={quote(s.feishu_oauth_scope, safe='')}&state={quote(state, safe='')}"
    )
    logger.info("飞书 OAuth 跳转生成: session=%s", session_id[:12])
    return {"auth_url": auth_url, "state": state, "session_id": session_id}


@router.get("/oauth/callback")
def feishu_oauth_callback(code: str, state: str, redirect: str = "/") -> RedirectResponse:
    """授权回调：校验 state → 换 token 拿 open_id → 绑定 → 302 回前端。"""
    s = Settings()
    if not (s.feishu_app_id and s.feishu_app_secret):
        raise HTTPException(status_code=503, detail="飞书未配置（缺 FEISHU_APP_ID/SECRET）")
    session_id = _parse_state(state, s.feishu_app_secret)
    if not session_id:
        raise HTTPException(status_code=400, detail="state 无效或已过期（可能是 CSRF 伪造）")
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 code")
    open_id = _exchange_token(code, s)
    if not open_id:
        raise HTTPException(status_code=502, detail="飞书授权换 token 失败，请重试")
    bind(session_id, open_id)
    target = _callback_target(s, redirect)
    sep = "&" if "?" in target else "?"
    return RedirectResponse(url=f"{target}{sep}feishu=bound", status_code=302)


@router.get("/binding")
def feishu_binding(session_id: str) -> dict:
    """查询网页会话是否已绑定飞书（前端情况1/情况2 的判定依据）。"""
    bound = is_bound(session_id)
    return {"bound": bound, "session_id": session_id}


__all__ = ["router", "_make_state", "_parse_state", "_exchange_token"]
