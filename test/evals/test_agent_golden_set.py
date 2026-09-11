"""E1-4 agent 金标集（eval/agent_golden_set.json）schema 校验 — 纯离线，无 DB/LLM。

校验：结构 / 规模（≥30）/ 词表 / 字段完备 / expected_tools 与 expected_sequence 一致 /
问题不重复。
"""

from __future__ import annotations

import json

from pathlib import Path

GOLDEN = Path(__file__).resolve().parent.parent.parent / "eval" / "agent_golden_set.json"

# deep_agent 实际工具词表（public_kb.tools.kb_search_as_tool / price_engine.tools）
TOOL_VOCAB = {"kb_search_tool", "price_query"}
QUERY_TYPES = {"kb_multi_hop", "price", "mixed"}
SOURCES = {"multi_hop_golden", "price_golden", "constructed"}


def _load_golden() -> list[dict]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "items" in data, "金标集须为 {description, items}"
    return data["items"]


def test_golden_structure_and_size():
    items = _load_golden()
    assert len(items) >= 30, f"E1-4 验收要求 ≥30 条，当前 {len(items)}"


def test_item_fields_and_vocab():
    for it in _load_golden():
        assert it["q"].strip(), "问题不能为空"
        assert it["query_type"] in QUERY_TYPES
        assert it["source"] in SOURCES
        seq = it["expected_sequence"]
        assert isinstance(seq, list) and seq, "expected_sequence 须为非空有序列表"
        assert set(seq) <= TOOL_VOCAB, f"工具超出词表: {set(seq) - TOOL_VOCAB}"
        tools = it["expected_tools"]
        assert set(tools) == set(seq), "expected_tools 须为 expected_sequence 的集合版"
        assert isinstance(it["key_entities"], list) and it["key_entities"], \
            "key_entities 须为非空列表"
        assert isinstance(it["rationale"], str) and it["rationale"], "rationale 不能为空"


def test_no_duplicate_questions():
    qs = [it["q"] for it in _load_golden()]
    assert len(qs) == len(set(qs)), "问题不得重复"


def test_golden_loadable_by_runner():
    """金标能被 runner 的 load_agent_golden 直接装载（契约一致性）。"""
    from evals.runners.agent_trace import load_agent_golden

    cases = load_agent_golden(GOLDEN)
    assert len(cases) == len(_load_golden())
    for c in cases:
        assert c["question"] and c["expected_tools"] and c["expected_sequence"]
