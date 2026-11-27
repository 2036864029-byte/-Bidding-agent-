"""channels — 第三方渠道接入包（并行线 A）。

与 `api/`（FastAPI HTTP 层）并列：HTTP 之外的外部入口/出口统一放这里，
`feishu/` 是第一个渠道（机器人问答在主线 P1~P5，任务进度推送在并行线 A 已落地）。
"""

from __future__ import annotations
