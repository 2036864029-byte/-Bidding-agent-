# 功能：出站 HTTP 的公共约定 —— **环回地址不走代理**。
"""背景（真实踩坑，2026-09-14）：

模型服务（embedding / rerank）常以 ``127.0.0.1:18xxx``（SSH 隧道，见
``.claude/ssh_tunnel_srv.py``）暴露给本机。而 ``httpx`` 与 ``requests`` 默认
``trust_env=True``，会把环境里的 ``HTTP_PROXY`` / ``HTTPS_PROXY`` 也套到**环回地址**上。

代理进程在客户端之外，够不到客户端自己的 localhost —— 于是连接必然失败。症状很坑人：

* 应用报 ``APIConnectionError`` / ``Connection error``，看起来像"模型服务没起来"；
* 但同机 ``curl http://127.0.0.1:18003`` 却是通的，于是误判为玄学；
* 同进程里 MySQL / Milvus / Redis 都正常 —— 因为它们用的客户端不读代理环境变量，
  **只有走 HTTP SDK 的模型调用中招**。

这里只把**环回**加入 ``NO_PROXY``；非环回地址仍按环境走代理（访问 DeepSeek、
SiliconFlow 等外网服务时可能正需要代理）。``httpx`` 与 ``requests`` 都认这个变量，
所以一处设置、两处生效。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

#: 视为「本机」的主机名（含 127.0.0.0/8 网段，见 is_loopback_url）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})

#: 需要补进 NO_PROXY 的条目
_NO_PROXY_ENTRIES = ("127.0.0.1", "localhost", "::1")


def is_loopback_url(url: str) -> bool:
    """URL 是否指向本机（localhost / ::1 / 127.0.0.0/8）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def ensure_no_proxy_for_loopback() -> bool:
    """把环回地址补进 ``NO_PROXY``（幂等）。

    Returns:
        True 表示本次真的改动了环境变量；False 表示本来就已覆盖。
    """
    raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    present = {e.lower() for e in entries}

    missing = [h for h in _NO_PROXY_ENTRIES if h.lower() not in present]
    if not missing:
        return False

    merged = ",".join(entries + missing)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged  # 部分库只认小写
    return True


def loopback_direct_http_client(timeout: float = 30.0):
    """构造 httpx.Client：**环回直连、其它地址仍走环境代理**。

    为什么不能只靠 ``NO_PROXY``（实测踩坑）：环境变量是否被采纳取决于客户端
    **构造时机**与库内部行为 —— 长驻进程里出现过"NO_PROXY 已设、httpx 解析出的
    mounts 也正确，实际请求仍走代理"的情况。显式 mount 不依赖任何环境状态，
    确定性更强，所以两条路都留着：环境变量给 ``requests`` 等库用，mount 给 httpx。

    Returns:
        未安装 httpx 时返回 None（调用方按需回退）。
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx 是既有依赖，正常不会走到
        return None

    direct = httpx.HTTPTransport()  # 不带 proxy：直连
    return httpx.Client(
        timeout=timeout,
        mounts={
            "http://127.0.0.1": direct,
            "http://localhost": direct,
            "http://[::1]": direct,
        },
    )


__all__ = [
    "LOOPBACK_HOSTS",
    "ensure_no_proxy_for_loopback",
    "is_loopback_url",
    "loopback_direct_http_client",
]
