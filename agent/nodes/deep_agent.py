"""agent.nodes.deep_agent — Deep Agent 三阶段管线（方案 Phase 6 / W3-3）。

终态 = Plan-and-Solve + Reflection 叠加在工具循环上：

  PLAN       规划：LLM 把复杂问题拆成 ≥2 个可执行步骤（JSON 计划；解析失败/简单
             问题降级为单步直查，不阻断本轮）
  EXECUTE    执行：按步骤调用工具，observation 摘要落轨迹（限长 OBSERVATION_HEAD_CHARS）
  SYNTHESIZE 综合：所有 observation 拼进 prompt，LLM 生成草稿（逐 token 流式 emit）
  REFLECT    反思：LLM 审视草稿 → adequate（采纳）/ revised（修订版终答）/ gap（缺口）

轨迹经 ``core.contracts.trace`` 落 ``business_result.data.trace``（JSON 安全 dict），
评测脚本据此归因：plan 步骤数（复杂问题 ≥2 步） / 每步 observation / 反思结论。

离线可测：llm / tools 均注入（fake LLM 模拟 JSON 计划、流式草稿、反思判定）。
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from core.contracts.trace import (
    AgentTrace,
    PlanStep,
    ReflectionRecord,
    StepRecord,
    trace_payload,
)
from core.streaming.sink import emit_token

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 4
OBSERVATION_HEAD_CHARS = 400

DEEP_AGENT_SYSTEM_PROMPT = (
    "你是招投标智能助手的深度综合 agent。你通过多步检索（法规知识 + 结构化数据）"
    "回答复杂问题；回答需有依据，引用工具观察结果，不臆造。"
)

PLAN_PROMPT = """你是招投标智能助手的深度规划 agent。把用户问题拆成可独立执行的检索步骤。
可用工具（每行「工具名：用途」，**必须按问题类型选对工具**）：
{tools}

要求：
1. 输出 **JSON 数组**，每项含 objective（目标说明）、tool（工具名）、query（给工具的自然语言查询）。
2. 简单问题只输出 1 步；需要「先查数据、再查法规」等多源组合的复杂问题拆 2~4 步。
3. 选工具看问题要什么：问企业/工商/处罚/不良记录/中标等**结构化事实** → price_query；
   问法律/法规/制度/条文/原则 → kb_search_tool；两者都要 → 都排进计划。
4. 只输出 JSON，不要其他文字。
用户问题：{question}
"""

SYNTHESIZE_PROMPT = """{persona}
基于以下检索观察结果回答用户问题。结合多步观察给出完整、有依据的回答；
若观察不足以回答，如实说明，不要臆造。
用户问题：{question}
规划与观察：
{observations}
"""

REFLECT_PROMPT = """{persona}
你是严谨的回答审校。检查草稿是否准确、完整、有依据（是否真的引用了观察结果）。
输出 **JSON**：{{"verdict": "adequate"|"revised"|"gap", "notes": "发现", "revised_answer": "修订后的完整回答"}}
verdict 含义：
  adequate  草稿准确完整，直接采用
  revised   草稿有错漏，给出修订版（revised_answer 必须给完整回答）
  gap       观察不足以回答，如实说明缺口
只输出 JSON，不要其他文字。
用户问题：{question}
检索观察：
{observations}
草稿回答：
{draft}
"""

# 工具调用默认参数（对齐 W3-3a 工具契约：kb 摘要 / price 限行）
_TOOL_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "kb_search_tool": {"summary": True, "top_k": 5},
    "price_query": {"max_rows": 10},
}


def _tool_map(tools: Sequence[BaseTool]) -> Dict[str, BaseTool]:
    return {t.name: t for t in tools}


# ── PLAN：规划 ────────────────────────────────────────────────


def _extract_json(text: str) -> Any:
    """从 LLM 输出中提取 JSON（容错 markdown 围栏 / 前后杂文）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if text.startswith("["):
        end = text.rfind("]")
        if end > 0:
            text = text[: end + 1]
    elif text.startswith("{"):
        end = text.rfind("}")
        if end > 0:
            text = text[: end + 1]
    return json.loads(text)


def _tool_lines(tools: Sequence[Any]) -> str:
    """工具名 + 描述（规划器据此区分 kb_search_tool 与 price_query）。

    兼容 str（仅名字，无描述）与 BaseTool / 鸭子类型工具（取 .name/.description）。
    """
    lines = []
    for t in tools:
        name = getattr(t, "name", None) or str(t)
        desc = (getattr(t, "description", "") or "").strip()
        lines.append(f"- {name}：{desc}" if desc else f"- {name}")
    return "\n".join(lines)


def _plan_question(
    question: str,
    llm: BaseChatModel,
    tools: Sequence[Any],
    max_steps: int = DEFAULT_MAX_STEPS,
) -> List[PlanStep]:
    """规划：LLM 输出 JSON 步骤；解析失败/非法 → 单步直查兜底（不阻断本轮）。

    `tools` 可为 BaseTool/鸭子类型工具（含 .name/.description）或纯工具名字符串。
    """
    tool_names = [getattr(t, "name", None) or str(t) for t in tools]
    prompt = PLAN_PROMPT.format(question=question, tools=_tool_lines(tools))
    try:
        resp = llm.invoke([SystemMessage(content=prompt)])
        raw = _extract_json(str(resp.content or ""))
        steps: List[PlanStep] = []
        for i, item in enumerate(raw or []):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if tool not in tool_names:
                continue
            query = str(item.get("query") or "").strip() or question
            objective = str(item.get("objective") or f"步骤{i + 1}").strip()
            steps.append(PlanStep(id=len(steps) + 1, objective=objective, tool=tool, query=query))
            if len(steps) >= max_steps:
                break
        if steps:
            return steps
    except Exception as e:  # JSON 解析/LLM 输出非法 → 单步兜底
        logger.warning("deep_agent 规划解析失败，降级单步直查: %s", e)
    return [PlanStep(id=1, objective="直接检索回答", tool=tool_names[0], query=question)]


# ── EXECUTE：执行 ─────────────────────────────────────────────


def _summarize_result(result: Any) -> str:
    """工具返回值 → 观察摘要（kb 按块、price 按记录，统一限长；空结果返回空串）。"""
    if isinstance(result, dict):
        chunks = result.get("chunks")
        if isinstance(chunks, list):
            if not chunks:
                return ""
            parts = []
            for c in chunks[:3]:
                head = (c.get("content") or "")[:120].replace("\n", " ")
                loc = " / ".join(x for x in (c.get("doc_name"), c.get("chapter")) if x)
                parts.append(f"[{loc}] {head}" if loc else head)
            text = "\n".join(parts)
        else:
            records = result.get("records")
            if isinstance(records, list):
                text = json.dumps(records[:3], ensure_ascii=False, default=str) if records else ""
            else:
                text = json.dumps(result, ensure_ascii=False, default=str)
    else:
        text = str(result)
    if len(text) > OBSERVATION_HEAD_CHARS:
        text = text[:OBSERVATION_HEAD_CHARS] + "…"
    return text


def _count_records(result: Any) -> int:
    if isinstance(result, dict):
        if isinstance(result.get("records"), list):
            return len(result["records"])
        if isinstance(result.get("chunks"), list):
            return len(result["chunks"])
    return 0


def _execute_step(step: PlanStep, tool_map: Dict[str, BaseTool]) -> StepRecord:
    """执行单步：按工具契约注入默认参数，观察摘要限长落轨迹。"""
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    tool = tool_map.get(step.tool)
    if tool is None:
        return StepRecord(
            step_id=step.id, tool=step.tool, query=step.query,
            status="error", observation_head=f"未知工具: {step.tool}", records_count=0,
        )
    args: Dict[str, Any] = {"question": step.query}
    args.update(_TOOL_DEFAULTS.get(step.tool, {}))
    try:
        result = tool.invoke(args)
    except Exception as e:  # 工具异常不中断后续步骤
        logger.warning("deep_agent 步骤 %d 工具执行失败: %s", step.id, e)
        return StepRecord(
            step_id=step.id, tool=step.tool, query=step.query,
            status="error", observation_head=f"工具错误: {e}", records_count=0,
            latency_ms=elapsed_ms(),
        )
    head = _summarize_result(result)
    return StepRecord(
        step_id=step.id, tool=step.tool, query=step.query,
        status="ok" if head else "empty", observation_head=head,
        records_count=_count_records(result),
        latency_ms=elapsed_ms(),
    )


def _execute_steps(
    plan: Sequence[PlanStep], tool_map: Dict[str, BaseTool]
) -> List[StepRecord]:
    """并发执行计划步骤（步间无数据依赖，工具调用 I/O 主导）。

    S4（Layer 2）：EXECUTE 顺序→并行压缩耗时；轨迹按 step_id 排序保持稳定契约。
    E4：逐条提交并显式带入上下文（线程池不复制），否则工具 span 脱离请求 trace。
    """
    with ThreadPoolExecutor(max_workers=min(max(1, len(plan)), 4)) as ex:
        futures = [
            ex.submit(contextvars.copy_context().run, _execute_step, st, tool_map)
            for st in plan
        ]
        records = [f.result() for f in futures]
    return sorted(records, key=lambda r: r.step_id)


def _observations_text(steps: List[StepRecord]) -> str:
    lines = []
    for s in steps:
        lines.append(f"步骤{s.step_id}（{s.tool}，query={s.query}）:\n{s.observation_head}")
    return "\n\n".join(lines)


# ── SYNTHESIZE：综合（流式）──────────────────────────────────


def _synthesize(
    question: str,
    steps: List[StepRecord],
    llm: BaseChatModel,
    persona: str,
) -> str:
    """综合：观察 → 草稿回答（逐 token 流式 emit；无 sink 零开销退化为累积）。"""
    prompt = SYNTHESIZE_PROMPT.format(
        persona=persona, question=question, observations=_observations_text(steps)
    )
    parts: List[str] = []
    for chunk in llm.stream([SystemMessage(content=prompt)]):
        text = getattr(chunk, "content", chunk)
        emit_token(str(text))
        parts.append(str(text))
    return "".join(parts).strip()


# ── REFLECT：反思 ─────────────────────────────────────────────


def _reflect(
    question: str,
    steps: List[StepRecord],
    draft: str,
    llm: BaseChatModel,
    persona: str,
) -> ReflectionRecord:
    """反思：LLM 审校草稿 → verdict + 可选修订（解析失败默认采纳草稿）。"""
    prompt = REFLECT_PROMPT.format(
        persona=persona, question=question,
        observations=_observations_text(steps), draft=draft,
    )
    try:
        resp = llm.invoke([SystemMessage(content=prompt)])
        data = _extract_json(str(resp.content or ""))
        if isinstance(data, dict):
            verdict = str(data.get("verdict") or "adequate")
            if verdict not in ("adequate", "revised", "gap"):
                verdict = "adequate"
            revised = data.get("revised_answer")
            return ReflectionRecord(
                verdict=verdict,
                notes=str(data.get("notes") or ""),
                revised_answer=str(revised).strip() if revised else None,
            )
    except Exception as e:
        logger.warning("deep_agent 反思解析失败，采纳草稿: %s", e)
    return ReflectionRecord(verdict="adequate", notes="反思解析失败，采纳草稿")


# ── 编排 ──────────────────────────────────────────────────────


def run_deep_agent(
    question: str,
    *,
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str = DEEP_AGENT_SYSTEM_PROMPT,
    max_steps: int = DEFAULT_MAX_STEPS,
    reflect: bool = True,
) -> Dict[str, Any]:
    """执行 Deep Agent 三阶段管线：规划 → 执行 → 综合 → 反思。

    Args:
        question: 用户问题。
        llm: 支持 invoke/stream 的对话模型（DeepSeek / 测试 fake）。
        tools: LangChain tool 列表（kb_search_as_tool / price_query_as_tool）。
        system_prompt: agent 人格（综合/反思 prompt 的 persona 前缀）。
        max_steps: 规划步骤上限。
        reflect: 是否执行反思阶段（False = 只到 Plan-and-Solve）。

    Returns:
        {"answer": str, "trace": dict（AgentTrace JSON），"rounds": int,
         "draft_answer": str}。
    """
    tool_map = _tool_map(tools)
    persona = system_prompt or DEEP_AGENT_SYSTEM_PROMPT

    # 1. PLAN（把工具名+描述给规划器，避免只凭名字误选）
    plan = _plan_question(question, llm, tools, max_steps)

    # 2. EXECUTE（并发执行，轨迹按 step_id 排序；每步观察限长落轨迹）
    steps = _execute_steps(plan, tool_map)

    # 3. SYNTHESIZE（流式草稿）
    draft = _synthesize(question, steps, llm, persona)

    # 4. REFLECT（修订版覆盖草稿为终答）
    reflection: Optional[ReflectionRecord] = None
    final = draft
    if reflect:
        reflection = _reflect(question, steps, draft, llm, persona)
        if reflection.verdict == "revised" and reflection.revised_answer:
            final = reflection.revised_answer

    trace = AgentTrace(
        plan=plan,
        steps=steps,
        reflection=reflection,
        rounds=1,
        mode="plan_and_solve" if not reflect else "plan_and_solve_reflect",
    )
    return {
        "answer": final,
        "trace": trace_payload(trace),
        "rounds": 1,
        "draft_answer": draft,
    }


# ── 节点形态 ──────────────────────────────────────────────────


def _default_tools() -> List[BaseTool]:
    from public_kb.tools import kb_search_as_tool
    from price_engine.tools import price_query_as_tool

    return [kb_search_as_tool(), price_query_as_tool()]


def _slot_llm_factory() -> BaseChatModel:
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot

    return create_llm_for_slot("deep_agent", Settings())


def build_deep_agent_node(
    *,
    llm: Optional[BaseChatModel] = None,
    llm_factory: Optional[Callable[[], BaseChatModel]] = None,
    tools: Optional[Sequence[BaseTool]] = None,
    system_prompt: str = DEEP_AGENT_SYSTEM_PROMPT,
    max_steps: int = DEFAULT_MAX_STEPS,
    reflect: bool = True,
    agent_name: str = "deep_agent",
) -> Callable[[dict, Optional[RunnableConfig]], dict]:
    """构建 Deep Agent 节点：(AgentState, config?) -> dict。

    Args:
        llm / llm_factory: 直接实例（测试注入）或惰性工厂（deep_agent 槽位）。
        tools: 注入工具列表（默认 kb_search_as_tool + price_query_as_tool）。
        system_prompt / max_steps / reflect: 透传 run_deep_agent。
        agent_name: business_result.branch 标识。
    """
    if llm is None and llm_factory is None:
        raise ValueError("llm 与 llm_factory 至少提供一个")
    tools = list(tools) if tools is not None else _default_tools()

    def node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        messages = state.get("messages", [])
        if not messages:
            return {
                "business_result": {
                    "branch": agent_name,
                    "answer": "抱歉，没有收到您的问题，请重新输入。",
                    "data": {},
                },
            }
        question = str(messages[-1].content)
        llm_ = llm if llm is not None else llm_factory()
        result = run_deep_agent(
            question, llm=llm_, tools=tools,
            system_prompt=system_prompt, max_steps=max_steps, reflect=reflect,
        )
        trace = result["trace"]
        reflection = trace.get("reflection") or {}
        logger.info(
            "deep_agent[%s]: plan=%d 步 reflect=%s",
            agent_name, len(trace.get("plan", [])), reflection.get("verdict"),
        )
        return {
            "business_result": {
                "branch": agent_name,
                "answer": result["answer"],
                "data": {"trace": trace, "draft_answer": result["draft_answer"]},
            },
            "messages": [AIMessage(content=result["answer"])],
        }

    return node


# ── 进程级惰性单例（registry/base 登记的 entry）─────────────

_lock = threading.Lock()
_node: Optional[Callable] = None


def _get_deep_agent_node():
    global _node
    if _node is None:
        with _lock:
            if _node is None:
                _node = build_deep_agent_node()
    return _node


def node_deep_agent(state: dict, config: Optional[dict] = None) -> dict:
    """deep_agent 节点入口（与既有节点统一接口，支持 config 透传）。"""
    return _get_deep_agent_node()(state, config)


def reset_deep_agent() -> None:
    """清空单例（测试/重载用）。"""
    global _node
    _node = None


__all__ = [
    "DEFAULT_MAX_STEPS",
    "DEEP_AGENT_SYSTEM_PROMPT",
    "run_deep_agent",
    "build_deep_agent_node",
    "node_deep_agent",
    "reset_deep_agent",
]
