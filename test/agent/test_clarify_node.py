"""Phase 3 澄清式反问节点测试（2026-09-16）。

覆盖：节点格式化（候选/原文透传、answer 文案）、空候选优雅降级、
输出护栏契约（candidates 必填）、to_reply 的 clarify 分支映射。
"""

from __future__ import annotations

from agent.nodes.clarify import (
    CLARIFY_ANSWER,
    CLARIFY_EMPTY_ANSWER,
    node_clarify,
)


def _state(candidates=None, original=None):
    return {
        "business_result": {
            "branch": "clarify",
            "data": {"candidates": candidates or [], "original": original},
        }
    }


def test_node_clarify_formats_answer_and_candidates():
    out = node_clarify(_state(
        ["招标方式有哪些？请逐一介绍", "公开招标和邀请招标的区别"],
        "具体介绍一下？",
    ))

    br = out["business_result"]
    assert br["branch"] == "clarify"
    assert br["answer"] == CLARIFY_ANSWER
    assert br["data"]["candidates"] == [
        "招标方式有哪些？请逐一介绍",
        "公开招标和邀请招标的区别",
    ]
    assert br["data"]["original"] == "具体介绍一下？"
    assert out["messages"][-1].content == CLARIFY_ANSWER  # 终态消息一致


def test_node_clarify_caps_candidates_and_filters_empty():
    out = node_clarify(_state(
        ["a", "", "b", "c", "d"],  # 超 3 条 + 空串
    ))
    assert out["business_result"]["data"]["candidates"] == ["a", "b", "c"]


def test_node_clarify_empty_candidates_graceful():
    out = node_clarify(_state([]))
    br = out["business_result"]
    assert br["data"]["candidates"] == []
    assert br["answer"] == CLARIFY_EMPTY_ANSWER


def test_node_clarify_missing_business_result_graceful():
    out = node_clarify({})
    assert out["business_result"]["data"]["candidates"] == []
    assert out["business_result"]["answer"] == CLARIFY_EMPTY_ANSWER


def test_clarify_passes_output_guardrails():
    from agent.guardrails.output import validate_business_result

    out = node_clarify(_state(["候选一"]))
    assert validate_business_result(out["business_result"]) == []


def test_to_reply_maps_clarify_branch():
    from core.protocol.reply import to_reply

    reply = to_reply({
        "branch": "clarify",
        "answer": CLARIFY_ANSWER,
        "data": {"candidates": ["候选一"], "original": "追问"},
    })
    assert reply.reply_type == "clarify"
    assert reply.error_code is None  # 澄清不是错误
    assert reply.data["candidates"] == ["候选一"]
