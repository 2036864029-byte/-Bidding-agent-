"""出口协议契约 —— `ReplyType` 是唯一真值，任何派生副本都必须与它一致。

**为什么需要**（本项目的真实事故，见 `api/schemas.py:29` 的注释）：
`ChatResponse.reply_type` 曾经**手抄**了一份字面量联合，且漏了
`deep_agent`/`safety` —— 第 3 项能力上线后，agent 路由的问题**每一个都 500**
（pydantic 校验失败）。抄一份的成本是 2 秒，漏掉一个的代价是全线不可用。

后端已改为从 `ReplyType` 派生。本文件把这个约定**变成断言**：
任何人再抄一份、或新增能力忘了登记出口协议，CI 立即红。
"""

from __future__ import annotations

from typing import get_args

from api.schemas import ChatResponse
from core.protocol.reply import ReplyType


def test_chat_response_enum_derives_from_reply_type():
    """`ChatResponse.reply_type` 的 JSON Schema 枚举 == `ReplyType`。

    回归：`api/schemas.py` 曾手抄一份且漏了 `deep_agent`/`safety` → 全 500。
    """
    schema = ChatResponse.model_json_schema()
    declared = set(schema["properties"]["reply_type"]["enum"])
    assert declared == set(get_args(ReplyType)), (
        "ChatResponse 的 reply_type 枚举与 core.protocol.reply.ReplyType 不一致。\n"
        "  修法：`reply_type: ReplyType` 从 ReplyType 派生，不要另抄字面量。"
    )


def test_branch_map_covers_every_reply_type():
    """`_BRANCH_TO_REPLY` 的**值域**必须覆盖 `ReplyType` 全集。

    防的是"定义了回复类型却没有任何分支能产出它" —— 前端会为它写一个永远
    走不到的渲染分支，或者更糟：某个分支产出它却忘了加映射，落到
    `.get(branch, "fallback")`，前端把"成功作答"当降级提示渲染
    （实测发生过：intent=deep_agent、error_code=None，reply_type 却是 fallback）。
    """
    from core.protocol.reply import _BRANCH_TO_REPLY

    produced = set(_BRANCH_TO_REPLY.values())
    declared = set(get_args(ReplyType))
    assert produced == declared, (
        f"branch → reply_type 映射与 ReplyType 不匹配。\n"
        f"  定义了但无分支产出：{sorted(declared - produced)}\n"
        f"  有分支产出但未声明：{sorted(produced - declared)}"
    )


def test_every_enabled_capability_has_reply_type():
    """注册表里**每个启用的能力**，都能在 branch 映射表里找到落点。

    **本文件最有价值的一条**：它把"registry 登记"与"出口协议登记"这两个
    必须同步的动作锚在一起。新增能力时只做前者（登记 spec + 节点）是**编得过、
    测得过**的 —— 直到线上用户提问落到 fallback 才暴露。这条断言让它在提交时失败。

    注：registry 的启用状态受 `CAPABILITY_ENABLED/DISABLED` 影响，
    而 conftest 已把两者钉为空（用 spec 声明默认值），故本断言是确定性的。
    """
    from agent.registry import get_default_registry
    from core.protocol.reply import _BRANCH_TO_REPLY

    missing = sorted(
        spec.id for spec in get_default_registry().enabled_specs()
        if spec.id not in _BRANCH_TO_REPLY
    )
    assert not missing, (
        f"以下能力已登记进 registry 但未在 _BRANCH_TO_REPLY 登记出口类型，"
        f"其回答会静默落 fallback：{missing}\n"
        f"  修法：在 core/protocol/reply.py 的 _BRANCH_TO_REPLY 加一条映射。"
    )
