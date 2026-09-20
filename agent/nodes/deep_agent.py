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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

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
#: 执行轮次上限（Plan-and-Execute）：1 = 首轮计划，2 = 首轮 + 至多一轮观察回灌补检索
DEFAULT_MAX_EXEC_ROUNDS = 2
OBSERVATION_HEAD_CHARS = 400

DEEP_AGENT_SYSTEM_PROMPT = (
    "你是招投标智能助手的深度综合 agent。你通过多步检索（用户上传的文件 + 法规知识 + "
    "结构化数据）回答复杂问题；回答需有依据，引用工具观察结果，不臆造。"
)

PLAN_PROMPT = """你是招投标智能助手的深度规划 agent。把用户问题拆成可独立执行的检索步骤。
可用工具（每行「工具名：用途」，**必须按问题类型选对工具**）：
{tools}

要求：
1. 输出 **JSON 数组**，每项含 objective（目标说明）、tool（工具名）、query（给工具的自然语言查询）。
2. 简单问题只输出 1 步；需要「先查数据、再查法规」等多源组合的复杂问题拆 2~4 步。
3. 选工具看问题要什么：
   - 问**用户本次上传的文件**（"这份标书""我上传的合同""附件里"…）→ read_file；
     问题中出现的文件名要作为 file_name 放进该步的 args；
   - 问企业/工商/处罚/不良记录/中标等**结构化事实** → price_query；
   - 问法律/法规/制度/条文/原则 → kb_search_tool；
   - 多源组合（如"这份标书写得合规吗"→ read_file 取文件条款 + kb_search_tool 查法规）→ 都排进计划。
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

#: 反思输出的标记行（主格式；见 REFLECT_PROMPT / `_parse_reflection`）
_REFLECT_VERDICT_RE = re.compile(r"VERDICT\s*[:：]\s*(adequate|revised|gap)", re.IGNORECASE)
_REFLECT_NOTES_RE = re.compile(r"NOTES\s*[:：]\s*(.*)")
#: 修订回答的分隔标记（其后全部内容即修订版；见 `_parse_reflection`）
REFLECT_ANSWER_SEP = "---ANSWER---"

REFLECT_PROMPT = """{persona}
你是严谨的回答审校。检查草稿是否准确、完整、有依据（是否真的引用了观察结果）。

**严格按下面格式输出，不要用 JSON、不要加别的说明文字**：
第 1 行：VERDICT: adequate 或 revised 或 gap
第 2 行：NOTES: 一句话说明发现（没有可留空）
仅当 VERDICT 为 revised 时，从第 3 行起先输出下面这行分隔符，再输出修订后的**完整回答**：
{sep}
<修订后的完整回答>

VERDICT 含义：
  adequate  草稿准确完整，直接采用
  revised   草稿有错漏，给出修订版（必须给出完整回答，不要只给差异）
  gap       观察不足以回答，如实说明缺口

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
    "read_file": {"top_k": 6},
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


#: 不暴露给规划器的参数名：由 query 字段承担，避免同一语义两个来源
_ARG_EXCLUDED: Set[str] = {"question"}

#: **允许 LLM 覆盖**的参数白名单；未登记的工具 = 不接受 LLM 传参。
#:
#: 为什么不是"schema 里有什么就让 LLM 调什么"——实测让 LLM 自行决定
#: `summary` / `top_k` 会扰动检索行为：引用覆盖率 **0.948 → 0.894**
#: （基线 n=3 vs 放开后 n=8，两组区间不重叠；temperature=0，可复现非抖动）。
#: 实测分布：16 题里 LLM 有 8 次把 `summary` 改成 False、9 次把 `top_k` 调到 8。
#: 这些是**系统调优旋钮**——`summary=True` / `top_k=5` 是刻意的 compact 契约
#: （代码注释原文："内容截断、省 rendered_context，供 agent 多次检索组合上下文"），
#: 交给 LLM 只增加方差、不增加能力。
#:
#: `file_name` 是唯一真正需要 LLM 决定的：它来自问题文本，代码无从推断。
#: 后续若要放开更多参数，须先用金标集量出对引用覆盖率的影响再登记。
_LLM_TUNABLE_ARGS: Dict[str, Set[str]] = {
    "read_file": {"file_name"},
}


def _tool_schema_params(tool: Any) -> Optional[Set[str]]:
    """工具 `args_schema` 声明过的参数名（剔除 question）。

    返回 **None** = 该工具没有 args_schema（鸭子类型工具 / 纯名字字符串）→
    调用方不做存在性校验，保持改造前行为（测试里的 fake tool 正是这种）。
    """
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", None)
    if not fields:
        return None
    return {str(n) for n in fields if str(n) not in _ARG_EXCLUDED}


def _clean_step_args(tool: str, raw: Any, tools: Sequence[Any]) -> Dict[str, Any]:
    """规划给出的 args → 只保留**白名单内且工具真实存在**的参数。

    两道过滤缺一不可：
      - 白名单：挡掉 LLM 自行改动系统调优旋钮（见 `_LLM_TUNABLE_ARGS` 的实测数据）
      - 存在性：挡掉 LLM 编造的参数名（否则工具直接抛 `unexpected keyword argument`）
    """
    if not isinstance(raw, dict):
        return {}
    allowed = _LLM_TUNABLE_ARGS.get(tool) or set()
    if not allowed:
        return {}
    by_name = {getattr(t, "name", None) or str(t): t for t in tools}
    schema_params = _tool_schema_params(by_name.get(tool))
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k).strip()
        if key not in allowed:
            continue
        if schema_params is not None and key not in schema_params:
            continue
        out[key] = v
    return out


def _parse_steps(
    raw: Any,
    *,
    tool_names: Sequence[str],
    tools: Sequence[Any],
    default_query: str,
    start_id: int = 1,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> List[PlanStep]:
    """LLM 输出的 JSON 步骤 → PlanStep 列表（PLAN / REPLAN 共用）。

    过滤规则：非法项跳过、工具名必须在候选集内、步数封顶。
    `start_id` 让补检索轮的步骤 id 接在已执行步骤之后（轨迹里 step_id 全局唯一）。
    """
    steps: List[PlanStep] = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in tool_names:
            continue
        query = str(item.get("query") or "").strip() or default_query
        objective = str(item.get("objective") or f"步骤{len(steps) + 1}").strip()
        steps.append(PlanStep(
            id=start_id + len(steps), objective=objective, tool=tool, query=query,
            args=_clean_step_args(tool, item.get("args"), tools),
        ))
        if len(steps) >= max_steps:
            break
    return steps


def _plan_question(
    question: str,
    llm: BaseChatModel,
    tools: Sequence[Any],
    max_steps: int = DEFAULT_MAX_STEPS,
) -> List[PlanStep]:
    """规划：LLM 输出 JSON 步骤；解析失败/非法 → 单步直查兜底（不阻断本轮）。

    `tools` 可为 BaseTool/鸭子类型工具（含 .name/.description）或纯工具名字符串。

    Phase 1：每步可带 `args`（由 LLM 给出，经 `_clean_step_args` 剔除未知参数名）。
    """
    tool_names = [getattr(t, "name", None) or str(t) for t in tools]
    prompt = PLAN_PROMPT.format(question=question, tools=_tool_lines(tools))
    try:
        resp = llm.invoke([SystemMessage(content=prompt)])
        steps = _parse_steps(
            _extract_json(str(resp.content or "")),
            tool_names=tool_names, tools=tools, default_query=question, max_steps=max_steps,
        )
        if steps:
            return steps
    except Exception as e:  # JSON 解析/LLM 输出非法 → 单步兜底
        logger.warning("deep_agent 规划解析失败，降级单步直查: %s", e)
    return [PlanStep(id=1, objective="直接检索回答", tool=tool_names[0], query=question)]


REPLAN_PROMPT = """你是招投标智能助手的深度规划 agent。下面的检索步骤**已经执行完毕**，
请判断还需要**补充**哪些检索才能完整回答用户问题。

已执行步骤与观察：
{observations}

可用工具：
{tools}

要求：
1. 观察已足够回答 → 输出空数组 **[]**。**不要为了凑步数而补检索。**
2. 确有缺口（某步空结果 / 缺法规依据 / 缺结构化数据）→ 输出 **JSON 数组**，
   每项含 objective、tool、query；**只列补充步骤，不要重复已执行过的查询**。
3. 补检索最多 {max_steps} 步。参数规则同规划阶段（read_file 的文件名放 args）。
4. 只输出 JSON，不要其他文字。
用户问题：{question}
"""


def _replan(
    question: str,
    executed: Sequence[StepRecord],
    llm: BaseChatModel,
    tools: Sequence[Any],
    max_steps: int = DEFAULT_MAX_STEPS,
) -> List[PlanStep]:
    """把已执行步骤的观察**回灌** LLM → 判断是否需要补检索。

    返回空列表 = 观察已足够（正常终止路径，不是错误）。
    异常/解析失败一律返回空 —— 补检索是**增益**而非必需，不能因此阻断本轮。
    """
    if not executed:
        return []
    tool_names = [getattr(t, "name", None) or str(t) for t in tools]
    prompt = REPLAN_PROMPT.format(
        observations=_observations_text(list(executed)),
        tools=_tool_lines(tools),
        max_steps=max_steps,
        question=question,
    )
    try:
        resp = llm.invoke([SystemMessage(content=prompt)])
        return _parse_steps(
            _extract_json(str(resp.content or "")),
            tool_names=tool_names, tools=tools, default_query=question,
            start_id=len(executed) + 1, max_steps=max_steps,
        )
    except Exception as e:  # 补检索失败不影响已有结果
        logger.warning("deep_agent 补检索规划失败，按观察已足够处理: %s", e)
        return []


# ── EXECUTE：执行 ─────────────────────────────────────────────

#: 观察摘要预算（按工具区分）：(块数上限, 每块字符上限, 总字符上限)。
#:
#: **为什么按工具区分**：不同工具"agent 需要多少内容"根本不同。
#: `read_file` 的目的就是把相关段落**原文**交给 agent（块 128~2000 字），
#: 用默认的 120 字/块会把事实截在句中——实测教训：「设备质保期：自验收合格
#: 之日起五年」正好落在第 121 字，agent 检索对了却回答"文件中未提及质保期年限"；
#: 另一题 LLM 自己说出"文件表述被截断（显示为「按合…」）"。
#: 而 `kb_search_tool` 的 120 字/块够看主旨（KB 块 700~900 字，且工具自带 summary 契约），
#: 维持原值以免无差别抬高 token 成本。
_OBSERVATION_BUDGETS: Dict[str, Tuple[int, int, int]] = {
    "read_file": (5, 800, 4000),
}
_DEFAULT_OBSERVATION_BUDGET: Tuple[int, int, int] = (3, 120, OBSERVATION_HEAD_CHARS)


def _summarize_result(result: Any, tool_name: str = "") -> str:
    """工具返回值 → 观察摘要（kb 按块、price 按记录；预算按工具区分）。

    摘要为空串 = 该步视为 `status="empty"`（见 `_execute_step`），
    也是 agent 判断"要不要补检索"的现成信号。
    """
    max_chunks, per_chunk, total_cap = _OBSERVATION_BUDGETS.get(
        tool_name, _DEFAULT_OBSERVATION_BUDGET
    )
    if isinstance(result, dict):
        chunks = result.get("chunks")
        if isinstance(chunks, list):
            if not chunks:
                return ""
            parts = []
            for c in chunks[:max_chunks]:
                head = (c.get("content") or "")[:per_chunk].replace("\n", " ")
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
    if len(text) > total_cap:
        text = text[:total_cap] + "…"
    return text


def _count_records(result: Any) -> int:
    if isinstance(result, dict):
        if isinstance(result.get("records"), list):
            return len(result["records"])
        if isinstance(result.get("chunks"), list):
            return len(result["chunks"])
    return 0


#: 单步最多记录多少条来源标识（够核算引用即可，不进 prompt）
_MAX_SOURCES = 8


def _extract_sources(result: Any) -> List[str]:
    """工具结果 → 来源标识列表（``doc_name｜chapter``），供「引用覆盖率」核算。

    只留**标识**不留正文：用于回答"答案里的引用是否来自**实际检索到**的东西"
    （可核验性）。`doc_qa` 结构上没有检索 → 其轨迹不存在 → 覆盖率恒 0，
    这正是要度量的结构性差异。
    """
    if not isinstance(result, dict):
        return []
    out: List[str] = []
    for c in (result.get("chunks") or [])[:_MAX_SOURCES]:
        if not isinstance(c, dict):
            continue
        label = "｜".join(
            str(c.get(k) or "").strip() for k in ("doc_name", "chapter") if str(c.get(k) or "").strip()
        )
        if label:
            out.append(label[:200])
    if not out:                       # price_query 等按记录返回
        for rec in (result.get("records") or [])[:_MAX_SOURCES]:
            if isinstance(rec, dict):
                label = "｜".join(
                    str(rec.get(k)) for k in ("company_name", "project_name", "name")
                    if rec.get(k)
                ) or str(rec)[:80]
            else:
                label = str(rec)[:80]
            if label:
                out.append(label)
    return out


def _execute_step(step: PlanStep, tool_map: Dict[str, BaseTool], round_id: int = 1) -> StepRecord:
    """执行单步：按工具契约注入默认参数，观察摘要限长落轨迹。"""
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    tool = tool_map.get(step.tool)
    if tool is None:
        return StepRecord(
            step_id=step.id, tool=step.tool, query=step.query, round=round_id,
            status="error", observation_head=f"未知工具: {step.tool}", records_count=0,
        )
    args: Dict[str, Any] = {"question": step.query}
    args.update(_TOOL_DEFAULTS.get(step.tool, {}))          # 兜底默认值
    # Phase 1：LLM 显式参数优先。规划期已剔除未知参数名与 question（`_clean_step_args`），
    # 此处再滤一次 question 只是防空 —— 它一旦被覆盖，工具会拿到错的问题文本。
    args.update({k: v for k, v in (step.args or {}).items() if k not in _ARG_EXCLUDED})
    #: 实际调用参数（剔 question）——与 PlanStep.args 比对可知哪些走了兜底
    called_args = {k: v for k, v in args.items() if k not in _ARG_EXCLUDED}
    try:
        result = tool.invoke(args)
    except Exception as e:  # 工具异常不中断后续步骤
        logger.warning("deep_agent 步骤 %d 工具执行失败: %s", step.id, e)
        return StepRecord(
            step_id=step.id, tool=step.tool, query=step.query, args=called_args,
            round=round_id,
            status="error", observation_head=f"工具错误: {e}", records_count=0,
            latency_ms=elapsed_ms(),
        )
    head = _summarize_result(result, step.tool)
    return StepRecord(
        step_id=step.id, tool=step.tool, query=step.query, args=called_args,
        round=round_id,
        status="ok" if head else "empty", observation_head=head,
        records_count=_count_records(result),
        latency_ms=elapsed_ms(),
        sources=_extract_sources(result),
    )


def _execute_steps(
    plan: Sequence[PlanStep], tool_map: Dict[str, BaseTool], round_id: int = 1
) -> List[StepRecord]:
    """并发执行计划步骤（步间无数据依赖，工具调用 I/O 主导）。

    S4（Layer 2）：EXECUTE 顺序→并行压缩耗时；轨迹按 step_id 排序保持稳定契约。
    E4：逐条提交并显式带入上下文（线程池不复制），否则工具 span 脱离请求 trace。

    Phase 2：轮内仍**并发**（保住 S4 收益）——回灌只发生在轮与轮之间。
    """
    with ThreadPoolExecutor(max_workers=min(max(1, len(plan)), 4)) as ex:
        futures = [
            ex.submit(contextvars.copy_context().run, _execute_step, st, tool_map, round_id)
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


def _parse_reflection(text: str) -> Optional[ReflectionRecord]:
    """解析反思输出 → ReflectionRecord；无法解析返回 None（调用方采纳草稿）。

    **主格式是「标记行 + 正文」而不是 JSON**：修订回答是长中文散文，要塞进 JSON
    字符串就得靠模型自行转义，实测稳定产出非法 JSON（`Expecting ',' delimiter`）
    → 过去只能降级为"采纳草稿"，也就是**答案根本没被审校**（Phase G-8 实测记录）。
    把长文本移出 JSON，就从结构上消除了这一类失败。

    兼容 JSON 输出（模型忽略格式要求时兜底；老格式不至于因此失效）。
    """
    raw = (text or "").strip()
    m = _REFLECT_VERDICT_RE.search(raw)
    if m is not None:
        verdict = m.group(1).lower()
        notes_m = _REFLECT_NOTES_RE.search(raw)
        answer: Optional[str] = None
        if REFLECT_ANSWER_SEP in raw:
            answer = raw.split(REFLECT_ANSWER_SEP, 1)[1].strip() or None
        return ReflectionRecord(
            verdict=verdict,
            notes=(notes_m.group(1).strip() if notes_m else ""),
            revised_answer=answer,
        )
    try:  # 兼容 JSON
        data = _extract_json(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "adequate")
    if verdict not in ("adequate", "revised", "gap"):
        verdict = "adequate"
    revised = data.get("revised_answer")
    return ReflectionRecord(
        verdict=verdict,
        notes=str(data.get("notes") or ""),
        revised_answer=str(revised).strip() if revised else None,
    )


def _reflect(
    question: str,
    steps: List[StepRecord],
    draft: str,
    llm: BaseChatModel,
    persona: str,
) -> ReflectionRecord:
    """反思：LLM 审校草稿 → verdict + 可选修订（解析失败默认采纳草稿）。"""
    prompt = REFLECT_PROMPT.format(
        persona=persona, question=question, sep=REFLECT_ANSWER_SEP,
        observations=_observations_text(steps), draft=draft,
    )
    try:
        resp = llm.invoke([SystemMessage(content=prompt)])
        record = _parse_reflection(str(resp.content or ""))
        if record is not None:
            return record
        logger.warning("deep_agent 反思输出无法解析（既非标记格式也非 JSON），采纳草稿")
    except Exception as e:
        logger.warning("deep_agent 反思调用失败，采纳草稿: %s", e)
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
    max_exec_rounds: int = DEFAULT_MAX_EXEC_ROUNDS,
    replan_enabled: bool = True,
) -> Dict[str, Any]:
    """执行 Deep Agent 管线：规划 → 执行（有界回灌轮）→ 综合 → 反思。

    Args:
        question: 用户问题。
        llm: 支持 invoke/stream 的对话模型（DeepSeek / 测试 fake）。
        tools: LangChain tool 列表（kb_search_as_tool / price_query_as_tool）。
        system_prompt: agent 人格（综合/反思 prompt 的 persona 前缀）。
        max_steps: 单轮规划/补检索的步骤上限。
        reflect: 是否执行反思阶段（False = 只到 Plan-and-Execute）。
        max_exec_rounds: 执行轮次上限（1 = 一次性计划，旧行为）。
        replan_enabled: False = 不补检索（等价 max_exec_rounds=1，便于回滚）。

    Returns:
        {"answer": str, "trace": dict（AgentTrace JSON），"rounds": int,
         "draft_answer": str}。

    Phase 2（Plan-and-Execute）：首轮按计划**并发**执行后，把观察回灌 LLM 判断是否
    需补检索（某步空结果/缺依据），至多 `max_exec_rounds` 轮。**轮内仍并发**，
    保住 S4 的并行收益；回灌只发生在轮与轮之间。
    """
    tool_map = _tool_map(tools)
    persona = system_prompt or DEEP_AGENT_SYSTEM_PROMPT
    budget = max(1, int(max_exec_rounds)) if replan_enabled else 1

    # 1. PLAN（把工具名+描述给规划器，避免只凭名字误选）
    plan = _plan_question(question, llm, tools, max_steps)
    planned = list(plan)

    # 2. EXECUTE（有界轮：第 1 轮走计划，后续轮由 REPLAN 依观察补齐）
    steps: List[StepRecord] = []
    batch = plan
    rounds_used = 0
    for round_id in range(1, budget + 1):
        if not batch:
            break
        rounds_used = round_id
        steps.extend(_execute_steps(batch, tool_map, round_id))
        if round_id >= budget:
            break
        batch = _replan(question, steps, llm, tools, max_steps)
        if batch:
            logger.info("deep_agent 第 %d 轮补检索：%d 步", round_id + 1, len(batch))

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
        plan=planned,
        steps=steps,
        reflection=reflection,
        rounds=rounds_used,
        mode=("plan_execute" if not reflect else "plan_execute_reflect")
        if budget > 1 else ("plan_and_solve" if not reflect else "plan_and_solve_reflect"),
    )
    return {
        "answer": final,
        "trace": trace_payload(trace),
        "rounds": rounds_used,
        "draft_answer": draft,
    }


# ── 节点形态 ──────────────────────────────────────────────────


def _default_tools() -> List[BaseTool]:
    from price_engine.tools import price_query_as_tool
    from public_kb.tools import kb_search_as_tool

    from ..doc_context import read_file_as_tool

    return [kb_search_as_tool(), price_query_as_tool(), read_file_as_tool()]


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
    max_exec_rounds: Optional[int] = None,
    replan_enabled: Optional[bool] = None,
    agent_name: str = "deep_agent",
) -> Callable[[dict, Optional[RunnableConfig]], dict]:
    """构建 Deep Agent 节点：(AgentState, config?) -> dict。

    Args:
        llm / llm_factory: 直接实例（测试注入）或惰性工厂（deep_agent 槽位）。
        tools: 注入工具列表（默认 kb_search_as_tool + price_query_as_tool）。
        system_prompt / max_steps / reflect: 透传 run_deep_agent。
        max_exec_rounds / replan_enabled: None = 每次调用读 Settings（可在不重启的
            前提下改 env 生效的前提是重启，但至少不必把常量写死在节点里）。
        agent_name: business_result.branch 标识。
    """
    if llm is None and llm_factory is None:
        raise ValueError("llm 与 llm_factory 至少提供一个")
    tools = list(tools) if tools is not None else _default_tools()

    def _rounds() -> Tuple[int, bool]:
        if max_exec_rounds is not None or replan_enabled is not None:
            return (
                max_exec_rounds if max_exec_rounds is not None else DEFAULT_MAX_EXEC_ROUNDS,
                replan_enabled if replan_enabled is not None else True,
            )
        from core.config.settings import Settings

        s = Settings()
        return s.deep_agent_max_exec_rounds, s.deep_agent_replan_enabled

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
        # Phase G-5 前置阻断 ②：原实现只看 messages[-1]，**看不到 API 层注入的
        # 文档上下文** → 不知道有文件可读，read_file 就永远不会被调用。
        # 这里只补**文件名清单**（不含正文——正文由 read_file 按需检索拉取），
        # 既是规划器选 read_file 的依据，也避免把整份文档塞进 prompt。
        from ..doc_context import session_file_names

        files = session_file_names(messages)
        if files:
            question = (
                f"{question}\n\n[本会话已上传文件（可用 read_file 工具读取）] "
                + "、".join(files)
            )
        llm_ = llm if llm is not None else llm_factory()
        rounds, replan = _rounds()
        result = run_deep_agent(
            question, llm=llm_, tools=tools,
            system_prompt=system_prompt, max_steps=max_steps, reflect=reflect,
            max_exec_rounds=rounds, replan_enabled=replan,
        )
        trace = result["trace"]
        reflection = trace.get("reflection") or {}
        logger.info(
            "deep_agent[%s]: plan=%d 步 exec_rounds=%d reflect=%s",
            agent_name, len(trace.get("plan", [])), trace.get("rounds", 0),
            reflection.get("verdict"),
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
                # ⚠️ 必须传 llm_factory：`build_deep_agent_node()` 无 llm 来源会直接
                # 抛 ValueError。此前长期传空 → 本节点经 registry 接线后**从未能执行**
                # （被 `_with_fallback` 吞成降级话术），因 `enabled=False` 从未暴露。
                # 端到端验收（走完整 AgentGraph）才发现，见第 3 项验收记录。
                _node = build_deep_agent_node(llm_factory=_slot_llm_factory)
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
    "DEFAULT_MAX_EXEC_ROUNDS",
    "DEEP_AGENT_SYSTEM_PROMPT",
    "run_deep_agent",
    "build_deep_agent_node",
    "node_deep_agent",
    "reset_deep_agent",
]
