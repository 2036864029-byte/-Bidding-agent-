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
