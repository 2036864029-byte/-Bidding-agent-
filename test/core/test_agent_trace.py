"""core.contracts.trace — Agent 轨迹结构化契约离线测试（W3-3）。

纯数据模型：验证 plan/steps/reflection 字段契约、JSON 序列化、核心模块零上层依赖。
"""

from __future__ import annotations

from core.contracts import (
    AgentTrace,
    PlanStep,
    ReflectionRecord,
    StepRecord,
    trace_payload,
)


def test_plan_step_contract():
    step = PlanStep(
        id=1,
        objective="查企业中标记录",
        tool="price_query",
        query="某某公司中标了哪些项目",
    )
    assert step.id == 1
    assert step.objective == "查企业中标记录"
    assert step.tool == "price_query"
    assert step.depends_on == []


def test_step_record_observation_head():
    record = StepRecord(
        step_id=1,
        tool="price_query",
        query="q",
        status="ok",
        observation_head="找到 2 条中标记录…",
        records_count=2,
        latency_ms=120,
    )
    assert record.status == "ok"
    assert record.records_count == 2
    assert record.latency_ms == 120


def test_reflection_record_revised():
    ref = ReflectionRecord(
        verdict="revised",
        notes="草稿引用了无关法条，需要补充时效性说明",
        revised_answer="修订后的回答",
    )
    assert ref.verdict == "revised"
    assert ref.revised_answer == "修订后的回答"


def test_trace_payload_json_safe():
    trace = AgentTrace(
        plan=[
            PlanStep(id=1, objective="目标一", tool="price_query", query="q1"),
            PlanStep(id=2, objective="目标二", tool="kb_search_tool", query="q2"),
        ],
        steps=[
            StepRecord(step_id=1, tool="price_query", query="q1",
                       observation_head="obs1", records_count=3),
            StepRecord(step_id=2, tool="kb_search_tool", query="q2",
                       observation_head="obs2", records_count=5),
        ],
        reflection=ReflectionRecord(verdict="adequate", notes=""),
        rounds=1,
        mode="plan_and_solve",
    )
    payload = trace_payload(trace)

    assert len(payload["plan"]) == 2
    assert len(payload["steps"]) == 2
    assert payload["steps"][0]["observation_head"] == "obs1"
    assert payload["reflection"]["verdict"] == "adequate"
    assert payload["rounds"] == 1
    assert payload["mode"] == "plan_and_solve"
    # JSON 安全：pydantic 标量可被 json 直接序列化
    import json

    json.dumps(payload, ensure_ascii=False)


def test_trace_defaults():
    payload = trace_payload(AgentTrace())
    assert payload["plan"] == []
    assert payload["steps"] == []
    assert payload["reflection"] is None
    assert payload["mode"] == "plan_and_solve"
