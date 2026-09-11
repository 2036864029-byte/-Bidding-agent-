"""evals.trace_feedback — E4-1 trace 采样回灌离线测试（不触网）。"""

from __future__ import annotations

import json

from evals.trace_feedback import (
    build_candidate,
    extract_answer,
    extract_branch,
    extract_question,
    flag_root,
    merge_into_golden,
    observed_tools,
)


def _ev(name, *, input="", output="", branch="", level="", status_message="", latency_ms=100):
    return {"name": name, "input": input, "output": output, "branch": branch,
            "level": level, "status_message": status_message, "latency_ms": latency_ms}


def _kb_trace(q="招标方式有哪些？", a="分为公开招标和邀请招标【来源1】"):
    return [
        _ev("node.supervisor_node", input=q, branch="?"),
        _ev("node.node_knowledge_qa", input=q, output=a, branch="knowledge_qa"),
    ]


def test_extract_fields():
    events = _kb_trace()
    assert extract_question(events) == "招标方式有哪些？"
    assert extract_answer(events).startswith("分为公开招标")
    assert extract_branch(events) == "knowledge_qa"


def test_observed_tools_from_branch():
    # 无 retrieval span → 按 branch 推断
    assert observed_tools(_kb_trace()) == ["kb_search_tool"]
    price = [_ev("node.node_price_inquiry", branch="price_inquiry")]
    assert observed_tools(price) == ["price_query"]
    # fallback / general_chat → 无工具
    assert observed_tools([_ev("node.node_fallback", branch="fallback")]) == []


def test_observed_tools_from_retrieval_spans():
    # 有 retrieval span → 用真实顺序，去连续重复
    events = [_ev("retrieval.kb_search"), _ev("retrieval.kb_search"), _ev("retrieval.price_query"),
              _ev("node.node_price_inquiry", branch="price_inquiry")]
    assert observed_tools(events) == ["kb_search_tool", "price_query"]


def test_flag_root_clean_and_empty():
    events = _kb_trace()
    assert flag_root({"latency_ms": 1200}, events) == []
    empty = [_ev("node.node_knowledge_qa", input="x", output="", branch="knowledge_qa")]
    assert "empty_answer" in flag_root({"latency_ms": 100}, empty)


def test_flag_root_fallback_and_slow_and_error():
    fb = [_ev("node.node_fallback", input="x", output="抱歉，功能暂时无法使用", branch="fallback")]
    flags = flag_root({"latency_ms": 100}, fb)
    assert "fallback" in flags and "empty_answer" not in flags

    ok = _kb_trace()
    assert "slow" in flag_root({"latency_ms": 30000}, ok)
    err = ok + [_ev("llm.call", level="ERROR")]
    assert "error" in flag_root({"latency_ms": 100}, err)


def test_flag_root_multi_atom_single_branch():
    """多原子问题仅走单分支 → 疑漏查，入选待复核。"""
    q = "某企业中标后不签合同有什么后果？同时，被列入不良记录还能投标吗？请分别说明。"
    events = [_ev("node.node_knowledge_qa", input=q, output="答", branch="knowledge_qa")]
    assert "multi_atom_single_branch" in flag_root({"latency_ms": 100}, events)
    # 单原子问题不误报
    single = [_ev("node.node_knowledge_qa", input="招标方式有哪几种？", output="答", branch="knowledge_qa")]
    assert "multi_atom_single_branch" not in flag_root({"latency_ms": 100}, single)


def test_build_candidate_shape():
    c = build_candidate({"trace_id": "t1", "latency_ms": 1500}, _kb_trace(), ["slow"])
    assert c["q"] == "招标方式有哪些？"
    assert c["source"] == "trace_feedback"
    assert c["review_status"] == "pending"
    assert c["expected_sequence"] == ["kb_search_tool"]
    assert c["expected_tools"] == ["kb_search_tool"]
    assert c["trace_ref"]["trace_id"] == "t1" and c["trace_ref"]["branch"] == "knowledge_qa"
    assert "slow" in c["rationale"]


def test_merge_rejects_unreviewed_and_dedups(tmp_path):
    golden = tmp_path / "g.json"
    golden.write_text(json.dumps({
        "description": "d", "items": [
            {"q": "已存在的问题", "query_type": "price", "expected_sequence": ["price_query"],
             "expected_tools": ["price_query"], "key_entities": ["x"], "rationale": "r",
             "source": "price_golden"},
        ]}, ensure_ascii=False), encoding="utf-8")

    cands = [
        {"q": "新问题A", "query_type": "mixed", "expected_sequence": ["price_query", "kb_search_tool"],
         "expected_tools": ["kb_search_tool", "price_query"], "key_entities": ["e"],
         "rationale": "r", "source": "trace_feedback", "review_status": "pending",
         "trace_ref": {"trace_id": "t"}},                       # 未复核 → 拒
        {"q": "已存在的问题", "query_type": "price", "expected_sequence": ["price_query"],
         "expected_tools": ["price_query"], "key_entities": [], "rationale": "r",
         "source": "trace_feedback", "review_status": "reviewed"},  # 重复 → 跳
        {"q": "新问题B", "query_type": "kb_multi_hop", "expected_sequence": ["kb_search_tool"],
         "expected_tools": ["kb_search_tool"], "key_entities": ["法"],
         "rationale": "r", "source": "trace_feedback", "review_status": "reviewed",
         "trace_ref": {"trace_id": "t2"}},                      # 复核 → 收
    ]
    res = merge_into_golden(cands, golden)
    assert res["added"] == 1 and res["added_q"] == ["新问题B"]
    assert res["skipped"] == 2

    data = json.loads(golden.read_text(encoding="utf-8"))
    qs = [it["q"] for it in data["items"]]
    assert qs == ["已存在的问题", "新问题B"]
    # 只写 schema 字段，丢弃 trace_ref / review_status
    new_item = data["items"][1]
    assert "trace_ref" not in new_item and "review_status" not in new_item
    assert set(new_item) == {"q", "query_type", "expected_sequence", "expected_tools",
                             "key_entities", "rationale", "source"}


def test_merge_no_change_writes_nothing(tmp_path):
    golden = tmp_path / "g.json"
    original = json.dumps({"description": "d", "items": []}, ensure_ascii=False)
    golden.write_text(original, encoding="utf-8")
    res = merge_into_golden([{"q": "x", "review_status": "pending"}], golden)
    assert res["added"] == 0 and golden.read_text(encoding="utf-8") == original
