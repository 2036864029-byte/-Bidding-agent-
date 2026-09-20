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
SOURCES = {"multi_hop_golden", "price_golden", "constructed", "trace_feedback"}


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


def test_answer_hit_normalizes_whitespace():
    """数字两侧的空格不能把正确回答判成失败（实测吃过亏）。

    agent 答的是"80 万元"，而金标 OR 组写的是"80万" → 严格 `in` 判 False，
    看起来像"agent 变差了"，其实是检查的问题。
    """
    from evals.runners.agent_trace import _answer_hit

    assert _answer_hit("投标保证金为 80 万元整。", [["80万", "捌拾万"]]) is True
    assert _answer_hit("金额为人民币 800,000.00 元", [["800,000", "800000"]]) is True
    assert _answer_hit("保证金是捌拾万元整", [["80万", "捌拾万"]]) is True
    # 逗号不是空白 → 不指望 "800000" 能匹配 "800,000"（OR 组里两种写法都要给）
    assert _answer_hit("金额为 800,000 元", [["800000"]]) is False
    # 未标注 → None（不入命中率分母）
    assert _answer_hit("任意回答", []) is None
    # 全组须命中
    assert _answer_hit("只有 5%", [["百分之五", "5%"], ["二十四个月"]]) is False


def test_cited_from_retrieval():
    """引用核算：答案引用了**实际检索到**的来源才算（doc_qa 无来源 → 恒 False）。

    量的是**可核验性**——这是两条路径的结构性差异，不是答案质量比较。
    """
    from evals.runners.agent_trace import _cited_from_retrieval, _trace_sources

    # 轨迹里带的来源
    trace = {"steps": [
        {"sources": ["招标投标法实施条例｜第四十七条", "某标书.pdf｜招标文件 > 第三章"]},
    ]}
    sources = _trace_sources(trace)
    assert len(sources) == 2

    # 引用了检索到的法规名 → True
    assert _cited_from_retrieval("依据《招标投标法实施条例》第四十七条…", sources) is True
    # 只引用了文件、没引法规 → 也算（来源里含文件名）
    assert _cited_from_retrieval("该标书第三章规定…", sources) is False  # "第三章"太短，不足 5 字
    # 泛泛而谈、没有具体来源 → False
    assert _cited_from_retrieval("一般来说应当依法办理。", sources) is False
    # 无来源（doc_qa 路径）→ 恒 False
    assert _cited_from_retrieval("依据《招标投标法》…", []) is False
