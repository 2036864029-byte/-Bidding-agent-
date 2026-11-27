"""channels.feishu.client — lark-oapi 装配（HTTP 客户端 + 长连接事件）。

两条连接都是**出站**（我们主动连飞书）：
- ``Client``（HTTP）：回复消息 / 打表情用（handler 注入）。
- ``ws.Client``（WebSocket 长连接）：订阅 ``im.message.receive_v1``，
  自带断线重连；**无 stop() 方法**，进程退出即关闭（执行计划 P5-5）。
"""

from __future__ import annotations

from typing import Any, Callable

import lark_oapi
from lark_oapi import Client, EventDispatcherHandler


def build_http_client(app_id: str, app_secret: str, domain: str) -> Client:
    """HTTP API 客户端（回复 / reaction / 推送等出站调用）。"""
    return (
        Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(domain)
        .log_level(lark_oapi.LogLevel.WARNING)
        .build()
    )


def build_event_handler(
    on_message: Callable[[Any], None],
) -> EventDispatcherHandler:
    """注册 im.message.receive_v1（长连接模式不需要 encrypt_key / verification_token）。"""
    return (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )


def build_ws_client(
    app_id: str,
    app_secret: str,
    event_handler: EventDispatcherHandler,
    domain: str,
    log_level: lark_oapi.LogLevel = lark_oapi.LogLevel.INFO,
) -> lark_oapi.ws.Client:
    """长连接 WebSocket 客户端（出站，免公网/内网穿透）。"""
    return lark_oapi.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        domain=domain,
        log_level=log_level,
    )


__all__ = ["build_http_client", "build_event_handler", "build_ws_client"]
