"""Agent 轨迹结构化 — plan/steps/observation/reflection 稳定契约（W3-3）。

Deep Agent 的阶段产物统一落 ``business_result.data.trace``，供评测脚本 / 可观测
消费。纯数据模型，core 层零依赖——agent / orchestration / scripts / 前端均可 import，
不触碰任何上层包（分层契约第 1 条）。

字段语义：
    plan          Plan-and-Solve 的规划步骤列表（≥2 步 = 复杂问题拆解）。
    steps         逐步执行记录，observation 摘要落 ``observation_head``（限长，
                  评测只看头部即可归因）。
    reflection    Reflection 阶段审视结论（verdict + 可选修订回答）。
    rounds        阶段循环轮次（synthesize→reflect 重试等）。
    mode          执行模式：plan_and_solve（规划+执行+综合）/
                  reflect（追加反思修订）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """规划步骤（Plan-and-Solve 的原子单元）。"""

    id: int = Field(..., description="步骤序号（从 1 起）")
    objective: str = Field(..., description="本步要达成的目标（自然语言）")
    tool: str = Field(..., description="本步调用的工具名")
    query: str = Field("", description="本步传入工具的查询/参数")
    depends_on: List[int] = Field(default_factory=list, description="前置步骤 id（预留）")


class StepRecord(BaseModel):
    """单步执行记录（observation 落 observation_head，正文不随轨迹透传）。"""

    step_id: int = Field(..., description="对应 PlanStep.id")
    tool: str = Field(..., description="实际调用工具名")
    query: str = Field("", description="实际传入查询/参数")
    status: str = Field("ok", description="ok / empty / error")
    observation_head: str = Field("", description="观察摘要（前 OBSERVATION_HEAD_CHARS 字）")
    records_count: int = Field(0, description="结果条数（0=无结果）")
    latency_ms: Optional[int] = Field(None, description="单步耗时（毫秒）")


class ReflectionRecord(BaseModel):
    """反思结论（Reflection 阶段：审视草稿 → verdict + 可选的修订）。"""

    verdict: str = Field("adequate", description="adequate（采纳草稿）/ revised（修订后）/ gap（有缺口）")
    notes: str = Field("", description="审视发现/说明")
    revised_answer: Optional[str] = Field(None, description="修订后的最终回答（None=采用草稿）")


class AgentTrace(BaseModel):
    """Deep Agent 轨迹（序列化后落 business_result.data.trace）。"""

    plan: List[PlanStep] = Field(default_factory=list, description="规划步骤")
    steps: List[StepRecord] = Field(default_factory=list, description="执行记录")
    reflection: Optional[ReflectionRecord] = Field(None, description="反思结论（可无）")
    rounds: int = Field(0, description="阶段循环轮次")
    mode: str = Field("plan_and_solve", description="执行模式")


def trace_payload(trace: AgentTrace) -> Dict[str, Any]:
    """AgentTrace → JSON 安全 dict（落 data.trace 用）。"""
    return trace.model_dump(mode="json")


__all__ = [
    "AgentTrace",
    "PlanStep",
    "ReflectionRecord",
    "StepRecord",
    "trace_payload",
]
