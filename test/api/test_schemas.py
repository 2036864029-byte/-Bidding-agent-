"""ChatRequest/ChatResponse schemas 校验测试（方案 Phase 2 / C-3、C-9）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import ChatRequest


def test_question_required_and_length_bounds():
    with pytest.raises(ValidationError):
        ChatRequest(question="")          # 空 → 422
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 2001)  # 超长 → 422
    # 合法默认值
    req = ChatRequest(question="招标方式有哪些？")
    assert req.session_id is None
    assert req.stream is False


def test_session_and_stream_overridable():
    req = ChatRequest(question="q", session_id="s1", stream=True)
    assert req.session_id == "s1"
    assert req.stream is True


def test_chat_response_accepts_every_reply_type():
    """回归：`ChatResponse.reply_type` 必须接受**出口协议里的每一个** reply_type。

    曾经它自己抄了一份字面量、漏了 `deep_agent`/`safety` → 第 3 项上线后
    agent 路由的问题**每个都 500**（pydantic 校验失败）。现已从 `ReplyType` 派生，
    本用例守住这条不变量。
    """
    from typing import get_args

    from api.schemas import ChatResponse
    from core.protocol.reply import ReplyType

    for rt in get_args(ReplyType):
        assert ChatResponse(answer="x", reply_type=rt).reply_type == rt
