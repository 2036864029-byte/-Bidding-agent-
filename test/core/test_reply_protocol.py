"""core.protocol 出口薄协议测试（方案 Phase 2 / C-1、C-2、C-9）。"""

from __future__ import annotations

from core.protocol.renderers.api import reply_to_response
from core.protocol.renderers.cli import render_reply
from core.protocol.reply import AgentReply, to_reply

# ── 5 分支 sample ────────────────────────────────────────────

def _br(branch: str, **over):
    base = {"branch": branch, "answer": f"{branch} 的回答", "data": {}}
    base.update(over)
    return base


def test_maps_five_branches_to_reply_types():
    samples = {
        "knowledge_qa": "kb_answer",
        "price_inquiry": "price_records",
        "general_chat": "chat",
        "doc_qa": "doc_qa",
        "fallback": "fallback",
    }
    for branch, rtype in samples.items():
        reply = to_reply(_br(branch), intent="x")
        assert reply.reply_type == rtype
        assert reply.answer == f"{branch} 的回答"


def test_unknown_branch_falls_back():
    reply = to_reply(_br("mystery"), intent="x")
    assert reply.reply_type == "fallback"


def test_kb_answer_carries_citations_and_data():
    br = {
        "branch": "knowledge_qa",
        "answer": "答案【来源1】",
        "data": {
            "sources": [{"doc": "法A"}],
            "citations": [{"context_index": 1, "doc_name": "法A", "text": "第一条"}],
            "citation_validation": {"all_passed": True},
        },
    }
    reply = to_reply(br, intent="knowledge_qa", latency_ms=123)
    assert reply.reply_type == "kb_answer"
    assert reply.citations[0]["doc_name"] == "法A"
    assert reply.trace == {"intent": "knowledge_qa", "latency_ms": 123}


def test_non_dict_data_becomes_none():
    reply = to_reply({"branch": "price_inquiry", "answer": "x", "data": "oops"})
    assert reply.data is None


def test_guardrail_count_into_trace():
    br = {"branch": "knowledge_qa", "answer": "a",
          "data": {"guardrails": [{"ok": False, "detail": "引用不足"}]}}
    reply = to_reply(br, intent="knowledge_qa")
    assert reply.trace["guardrail_count"] == 1


def test_api_renderer_shape():
    reply = to_reply(
        {"branch": "general_chat", "answer": "你好", "data": None},
        intent="general_chat", latency_ms=5,
    )
    resp = reply_to_response(reply)
    assert resp["answer"] == "你好"
    assert resp["intent"] == "general_chat"
    assert resp["reply_type"] == "chat"
    assert resp["latency_ms"] == 5
    assert resp["citations"] == []


def test_cli_renderer_citations_block():
    reply = to_reply({
        "branch": "knowledge_qa",
        "answer": "答案【来源1】",
        "data": {"citations": [{"context_index": 1, "doc_name": "法A",
                                "chapter": "第一章", "text": "招标方式分为公开和邀请。"}]},
    }, intent="knowledge_qa")
    out = render_reply(reply)
    assert "答案【来源1】" in out
    assert "【来源1】法A" in out
    assert "原文: 招标方式分为公开和邀请。" in out


def test_cli_renderer_agentreply_direct():
    reply = AgentReply(reply_type="chat", answer="你好")
    assert render_reply(reply) == "你好"


def test_deep_agent_branch_maps_to_own_reply_type():
    """第 3 项上线后新增：deep_agent 必须有独立 reply_type。

    否则落到 `.get(branch, "fallback")` 兜底 → **前端把「agent 成功作答」当降级提示
    渲染**（实测：intent=deep_agent、error_code=None，reply_type 却是 "fallback"）。
    """
    reply = to_reply({"branch": "deep_agent", "answer": "依据…", "data": {"trace": {}}})

    assert reply.reply_type == "deep_agent"
    assert reply.error_code is None
