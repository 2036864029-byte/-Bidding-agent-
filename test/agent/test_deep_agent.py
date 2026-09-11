"""agent.deep_agent — Deep Agent 三阶段管线离线测试（W3-3）。

不触碰网络：fake LLM 模拟「JSON 规划 → 流式草稿 → JSON 反思」；fake tool 返回固定
结果并记录调用参数。覆盖：复杂问题 ≥2 步规划、反思修订、规划解析失败单步兜底、
工具异常/未知工具不中断、轨迹契约、流式 token、节点形态、registry/base 接线。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes.deep_agent import build_deep_agent_node, run_deep_agent


# ── 夹具 ──────────────────────────────────────────────────────

class _RecTool:
    """记录调用参数的鸭子类型 tool（BaseTool 最小可调用面）。"""

    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls: list[dict] = []

    def invoke(self, args):
        self.calls.append(dict(args))
        return self._result


class _FakeDeepLLM:
    """fake LLM：invoke 按 prompt 分派规划/反思 JSON，stream 返回草稿 token。"""

    def __init__(self, plan_json, draft="综合草稿回答。", reflection_json=None):
        self._plan_json = plan_json
        self._draft = draft
        self._reflection_json = reflection_json
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        prompt = messages[0].content
        if "深度规划" in prompt:
            return AIMessage(content=self._plan_json)
        if "审校" in prompt:
            return AIMessage(
                content=self._reflection_json
                if self._reflection_json is not None
                else '{"verdict": "adequate", "notes": ""}'
            )
        return AIMessage(content="")

    def stream(self, messages):
        for ch in self._draft:
            yield ch


def _kb_tool():
    return _RecTool(
        "kb_search_tool",
        {"chunks": [{"content": "中标人应当依法签订合同", "doc_name": "招标投标法", "chapter": "第四章"}]},
    )


def _price_tool():
    return _RecTool("price_query", {"records": [{"project_name": "智慧园区项目"}], "total_found": 1})


# ── 三阶段管线 ────────────────────────────────────────────────

def test_complex_question_plans_2plus_steps():
    """验收①：复杂问题自主规划 ≥2 步，逐步执行，轨迹可归因。"""
    llm = _FakeDeepLLM(
        plan_json=(
            '[{"objective": "查企业中标记录", "tool": "price_query", "query": "A公司中标了哪些项目"},'
            ' {"objective": "查相关法规", "tool": "kb_search_tool", "query": "中标后签订合同的法律规定"}]'
        ),
        draft="A 公司近期中标 1 个项目；按招标投标法，中标后应依法签订合同。",
        reflection_json='{"verdict": "adequate", "notes": ""}',
    )
    kb = _kb_tool()
    price = _price_tool()

    out = run_deep_agent("A公司最近中标项目涉及哪些法律风险？", llm=llm, tools=[kb, price])

    assert len(out["trace"]["plan"]) == 2
    assert [p["tool"] for p in out["trace"]["plan"]] == ["price_query", "kb_search_tool"]
    assert len(price.calls) == 1 and len(kb.calls) == 1
    # W3-3a 工具契约默认参数注入
    assert kb.calls[0]["summary"] is True
    assert price.calls[0]["max_rows"] == 10
    # 轨迹可消费：每步 observation + records_count + 反思结论
    assert out["trace"]["steps"][0]["observation_head"] != ""
    assert out["trace"]["steps"][0]["records_count"] == 1
    assert out["trace"]["steps"][1]["tool"] == "kb_search_tool"
    assert out["trace"]["reflection"]["verdict"] == "adequate"
    assert out["answer"] == llm._draft


def test_reflection_revises_answer():
    """Reflection 判定 revised → 修订版为终答，草稿保留在轨迹。"""
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查法规", "tool": "kb_search_tool", "query": "公开招标"}]',
        draft="草稿回答。",
        reflection_json='{"verdict": "revised", "notes": "遗漏依据", "revised_answer": "修订后的完整回答。"}',
    )

    out = run_deep_agent("q", llm=llm, tools=[_kb_tool()])

    assert out["answer"] == "修订后的完整回答。"
    assert out["draft_answer"] == "草稿回答。"
    assert out["trace"]["reflection"]["verdict"] == "revised"
    assert out["trace"]["reflection"]["revised_answer"] == "修订后的完整回答。"


def test_no_reflection_mode():
    """reflect=False → 只到 Plan-and-Solve（mode 标记 + reflection 为空）。"""
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查法规", "tool": "kb_search_tool", "query": "q"}]',
        draft="草稿。",
    )

    out = run_deep_agent("q", llm=llm, tools=[_RecTool("kb_search_tool", {"chunks": []})], reflect=False)

    assert out["answer"] == "草稿。"
    assert out["trace"]["mode"] == "plan_and_solve"
    assert out["trace"]["reflection"] is None


# ── 规划健壮性 ────────────────────────────────────────────────

def test_plan_parse_failure_falls_back_single_step():
    llm = _FakeDeepLLM(plan_json="不是JSON", draft="兜底回答。")

    out = run_deep_agent("q", llm=llm, tools=[_kb_tool()])

    assert len(out["trace"]["plan"]) == 1
    assert out["trace"]["plan"][0]["tool"] == "kb_search_tool"
    assert out["trace"]["plan"][0]["query"] == "q"
    assert out["answer"] == "兜底回答。"


def test_planner_filters_invalid_tools():
    """规划里出现未知工具 → 该步被过滤，只保留合法步骤。"""
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "有效", "tool": "kb_search_tool", "query": "qa"},'
                  ' {"objective": "无效", "tool": "nope", "query": "qb"}]',
        draft="回答。",
    )

    out = run_deep_agent("q", llm=llm, tools=[_kb_tool()])

    assert len(out["trace"]["plan"]) == 1
    assert out["trace"]["plan"][0]["tool"] == "kb_search_tool"
    assert len(out["trace"]["steps"]) == 1


def test_tool_error_does_not_break_pipeline():
    class _BoomTool:
        name = "price_query"

        def invoke(self, args):
            raise RuntimeError("db down")

    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查数据", "tool": "price_query", "query": "q"}]',
        draft="降级回答。",
    )

    out = run_deep_agent("q", llm=llm, tools=[_BoomTool()])

    assert out["trace"]["steps"][0]["status"] == "error"
    assert "工具错误" in out["trace"]["steps"][0]["observation_head"]
    assert out["answer"] == "降级回答。"  # 综合阶段仍执行


def test_empty_observation_status_empty():
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查", "tool": "kb_search_tool", "query": "q"}]',
        draft="无结果。",
    )

    out = run_deep_agent("q", llm=llm, tools=[_RecTool("kb_search_tool", {"chunks": []})])

    assert out["trace"]["steps"][0]["status"] == "empty"
    assert out["trace"]["steps"][0]["records_count"] == 0


# ── 流式 ──────────────────────────────────────────────────────

def test_streams_draft_tokens_to_sink():
    from core.streaming.sink import StreamSink, reset_active_sink, set_active_sink

    sink = StreamSink()
    token = set_active_sink(sink)
    try:
        llm = _FakeDeepLLM(
            plan_json='[{"objective": "查法规", "tool": "kb_search_tool", "query": "q"}]',
            draft="流式草稿回答。",
        )
        out = run_deep_agent("q", llm=llm, tools=[_kb_tool()])
    finally:
        reset_active_sink(token)

    tokens = []
    while True:
        ev = sink.get(timeout=0.01)
        if ev is None:
            break
        if ev[0] == "token":
            tokens.append(ev[1])
    assert "".join(tokens) == "流式草稿回答。"
    assert out["answer"] == "流式草稿回答。"


# ── 节点形态 ──────────────────────────────────────────────────

def test_build_deep_agent_node_runs_with_injected_deps():
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查企业", "tool": "price_query", "query": "A公司中标"}]',
        draft="A 公司中标 1 个项目。",
    )
    node = build_deep_agent_node(llm=llm, tools=[_price_tool()])

    out = node({"messages": [HumanMessage(content="A公司中标了哪些项目？")]})

    br = out["business_result"]
    assert br["branch"] == "deep_agent"
    assert "中标" in br["answer"]
    assert br["data"]["trace"]["plan"][0]["tool"] == "price_query"
    assert br["data"]["trace"]["reflection"]["verdict"] == "adequate"
    assert out["messages"][0].content == br["answer"]


def test_build_deep_agent_node_empty_messages():
    node = build_deep_agent_node(llm=_FakeDeepLLM(plan_json="[]"), tools=[_kb_tool()])
    out = node({"messages": []})
    assert "没有收到您的问题" in out["business_result"]["answer"]


def test_build_deep_agent_node_requires_llm_source():
    import pytest

    with pytest.raises(ValueError):
        build_deep_agent_node(tools=[_kb_tool()])


# ── 登记与接线（registry enabled=False 预留 + entry 已登记）───

def test_deep_agent_registry_reserved_disabled():
    from agent.registry import get_default_registry

    spec = get_default_registry().get("deep_agent")
    assert spec is not None
    assert spec.enabled is False          # 预留防误路由（同 doc_agent）
    assert spec.model_slots == ["deep_agent"]
    assert spec.tools == ["kb_search", "price_query"]


def test_deep_agent_entry_registered_in_base():
    from agent.nodes.base import get_node_spec

    assert get_node_spec("deep_agent") is not None


def test_deep_agent_enabled_registry_wires_without_graph_edits():
    """启用 deep_agent 后 graph/router 零改动自动接线（registry 数据驱动）。"""
    from agent.graph import build_graph
    from agent.nodes import node_deep_agent
    from agent.nodes.base import register
    from agent.registry import CapabilityRegistry, CapabilitySpec, seed_builtins

    reg = CapabilityRegistry()
    seed_builtins(reg)
    reg.register(CapabilitySpec(
        id="deep_agent", node="deep_agent", enabled=True,
        route_desc="深度综合问答", route_tool_doc="deep agent 深度综合",
        examples=["综合问题"], model_slots=["deep_agent"],
        tools=["kb_search", "price_query"],
    ))
    register("deep_agent", node_deep_agent)  # 装配层登记 entry（base 已 seed）

    compiled = build_graph(llm=object(), registry=reg)
    assert "deep_agent" in compiled.get_graph().nodes.keys()


# ── PLAN 提示词注入工具描述（修复：只凭名字会误选，price_query 被 kb 顶掉）──


class _DescTool:
    """带描述的最小鸭子类型工具。"""

    def __init__(self, name, description):
        self.name = name
        self.description = description


class _PromptCaptureLLM:
    """捕获规划 prompt 的 fake LLM（返回空计划 → 走单步兜底）。"""

    def __init__(self, plan_json="[]"):
        self._plan_json = plan_json
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        return AIMessage(content=self._plan_json)


def test_plan_prompt_includes_tool_descriptions():
    from agent.nodes.deep_agent import _plan_question

    llm = _PromptCaptureLLM()
    tools = [
        _DescTool("kb_search_tool", "检索招投标公共知识库（法规/招标知识）"),
        _DescTool("price_query", "结构化业务数据查询：企业工商信息、不良记录、中标情报"),
    ]
    _plan_question("安徽海纳信息科技有限公司的工商信息有哪些？", llm, tools)

    prompt = llm.prompts[0]
    assert "price_query" in prompt and "kb_search_tool" in prompt
    assert "结构化业务数据查询" in prompt      # price_query 描述
    assert "检索招投标公共知识库" in prompt      # kb 描述


def test_plan_accepts_plain_tool_names_and_fallback():
    """兼容纯工具名字符串（diag 脚本用法）；空计划 → 单步兜底用第一个工具。"""
    from agent.nodes.deep_agent import _plan_question

    llm = _PromptCaptureLLM()
    plan = _plan_question("问题", llm, ["kb_search_tool"])
    assert "kb_search_tool" in llm.prompts[0]
    assert plan[0].tool == "kb_search_tool"      # 空 JSON → 兜底 tool_names[0]

