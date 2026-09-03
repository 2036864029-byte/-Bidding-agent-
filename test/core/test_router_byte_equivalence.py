"""P4 router 动态化 byte-equal 回归：生成器输出 == 改动前冻结真值。

真值来自 scripts/freeze_router_golden.py（router 改造前对现状 5 个 route_* tool
与 ROUTER_SYSTEM_PROMPT 的原样抓取），存 test/golden/。任何注册表路由原文或
生成器的改动若改变给 LLM 的字节，此文件即红——把「router 意图集不降」在离线层
固化成可回归断言。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.registry import get_default_registry, reset_default_registry
from agent.router import (
    build_decision_model,
    build_route_tools,
    build_system_prompt,
    tool_intent_map,
)

GOLDEN = Path(__file__).resolve().parent.parent.parent / "test" / "golden"


def _default_caps():
    reset_default_registry()
    return get_default_registry().enabled_specs()


def test_system_prompt_byte_identical_to_golden():
    prompt = build_system_prompt(_default_caps())
    golden = (GOLDEN / "router_system_prompt.txt").read_text(encoding="utf-8")
    assert prompt == golden, "系统提示词与冻结真值不一致（行为漂移）"


def test_route_tools_byte_identical_to_golden():
    tools = build_route_tools(_default_caps())
    golden = json.loads((GOLDEN / "router_tools_golden.json").read_text(encoding="utf-8"))
    assert len(tools) == len(golden) == 5
    for t, g in zip(tools, golden):
        assert t.name == g["name"], f"tool 名漂移: {t.name} != {g['name']}"
        assert t.description == g["description"], f"tool {t.name} 描述漂移"
        assert t.args == g["args"], f"tool {t.name} args schema 漂移"


def test_tool_intent_map_covers_candidates():
    caps = _default_caps()
    m = tool_intent_map(caps)
    assert set(m) == {f"route_{c.id}" for c in caps}
    assert m["route_fallback"] == "fallback"


def test_decision_model_restricts_to_enabled_candidates():
    caps = _default_caps()
    model = build_decision_model([c.id for c in caps])
    enum = model.model_json_schema()["properties"]["intent"]["enum"]
    # 默认注册表 echo_demo 预留 disabled → 不进入候选
    assert enum == ["knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback"]
    m = model(intent="fallback", reason="x")
    assert m.intent == "fallback"
    try:
        model(intent="echo_demo", reason="x")
    except Exception:
        pass
    else:
        raise AssertionError("echo_demo 未 enabled 却可通过 schema 校验")
