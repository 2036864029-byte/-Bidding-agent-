"""前后端契约 —— 前端类型联合必须与后端 `ReplyType` 同步。

**为什么需要**：后端已经把 `ReplyType` 收敛成唯一真值（`core/protocol/reply.py`），
但**前端仍是手抄**（`frontend/src/types/chat.ts` 的字面量联合）。后端加一个
`reply_type`、前端不跟着改 → 前端落到 fallback 渲染分支，**不报错、不告警、
无测试失败**，只是新能力的回答被当降级提示展示。这类"静默失效"正是契约测试
存在的理由。

**为什么用断言而不是 codegen**：从后端生成 TS 类型会引入构建期耦合与生成物
同步问题（生成文件要不要入库？谁负责重跑？）。断言一致性达到同样效果，
且**失败信息能直接告诉人该改哪一行** —— 对一个只有一处联合的文件，这更简单。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from core.protocol.reply import ReplyType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHAT_TS = _REPO_ROOT / "frontend" / "src" / "types" / "chat.ts"

#: 匹配 `export type ReplyType = 'a' | 'b' | ...;`（可跨行）
_UNION_RE = re.compile(r"export type ReplyType\s*=\s*(.+?);", re.DOTALL)
#: 联合成员（单引号字符串字面量）
_MEMBER_RE = re.compile(r"'([a-z_]+)'")


def _frontend_reply_types() -> set[str]:
    source = _CHAT_TS.read_text(encoding="utf-8")
    match = _UNION_RE.search(source)
    assert match, (
        f"未能在 {_CHAT_TS.relative_to(_REPO_ROOT)} 里定位 `export type ReplyType = ...`。\n"
        f"  前端类型结构变了？本测试的正则需要同步更新。"
    )
    return set(_MEMBER_RE.findall(match.group(1)))


def test_frontend_reply_type_union_matches_backend():
    """前端 `ReplyType` 联合 == 后端 `ReplyType`。"""
    backend = set(get_args(ReplyType))
    frontend = _frontend_reply_types()

    assert frontend == backend, (
        "前后端 reply_type 契约漂移。\n"
        f"  后端有、前端无：{sorted(backend - frontend)}\n"
        f"  前端有、后端无：{sorted(frontend - backend)}\n"
        f"  修法：更新 {_CHAT_TS.relative_to(_REPO_ROOT)} 的 ReplyType 联合，"
        f"并同步前端的渲染分支注册表。"
    )
