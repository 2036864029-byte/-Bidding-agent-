"""channels.feishu.session — open_id/chat_id → 后端会话映射（主线机器人问答）。

飞书场景 → session_id（后端 MemorySaver 检查点键，见 api/server.py _resolve_session_id）：
- 私聊（chat_type=p2p）  → ``fs-p2p-<open_id>``（按人隔离，多轮上下文）
- 群聊（chat_type=group）→ ``fs-group-<chat_id>``（群内共享同一上下文）

``X-Tenant-ID`` 一律用 ``feishu:<open_id>`` —— 按人限流与隔离，
而非按群/按整个通道（否则一个飞书用户刷爆就把其他人全 429，见执行计划 R3）。
"""

from __future__ import annotations

_P2P_PREFIX = "fs-p2p-"
_GROUP_PREFIX = "fs-group-"
_TENANT_PREFIX = "feishu:"


def session_id_for(open_id: str, chat_id: str = "", chat_type: str = "p2p") -> str:
    """open_id/chat_id → 后端会话 id。非 p2p 一律按群共享。"""
    if chat_type == "group":
        return f"{_GROUP_PREFIX}{chat_id}"
    return f"{_P2P_PREFIX}{open_id}"


def tenant_id_for(open_id: str) -> str:
    """open_id → X-Tenant-ID（按人限流/隔离的键）。"""
    return f"{_TENANT_PREFIX}{open_id}"


__all__ = ["session_id_for", "tenant_id_for"]
