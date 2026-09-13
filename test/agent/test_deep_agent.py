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
    assert spec.tools == ["kb_search", "price_query", "read_file"]


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



# ── Phase G-5/G-6：文档能力接线（read_file 工具 + 文件名可见性）────────


def test_default_tools_includes_read_file():
    """前置阻断 ①：原先工具只有 kb_search + price_query，deep_agent 无任何读文件手段。"""
    from agent.nodes.deep_agent import _default_tools

    names = {t.name for t in _default_tools()}

    assert {"kb_search_tool", "price_query", "read_file"} <= names


def test_read_file_has_tool_defaults():
    """执行器固定传 question=…，其余参数由 _TOOL_DEFAULTS 补——新工具必须登记。"""
    from agent.nodes.deep_agent import _TOOL_DEFAULTS

    assert _TOOL_DEFAULTS["read_file"] == {"top_k": 6}


class _CapturePlanLLM:
    """捕获规划 prompt 的 fake（返回空计划 → 单步兜底，避免依赖 LLM 行为）。"""

    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[0].content)
        return AIMessage(content="[]")

    def stream(self, messages):
        yield "草稿"


def _doc_state_with_file(question="这份标书的保证金要求合规吗？"):
    from langchain_core.messages import SystemMessage

    from agent.nodes.doc_qa import DOC_CONTEXT_MARKER

    return {"messages": [
        SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：招标文件.pdf]\n第三章 保证金…"),
        HumanMessage(content=question),
    ]}


def test_node_makes_injected_file_names_visible_to_planner():
    """前置阻断 ②：原实现只取 messages[-1]，看不到注入的文件 → read_file 永不会被规划。

    只注入**文件名清单**（不含正文）：正文由 read_file 按需检索拉取。
    """
    llm = _CapturePlanLLM()
    node = build_deep_agent_node(llm=llm, tools=[_kb_tool()], reflect=False)

    node(_doc_state_with_file())

    prompt = llm.prompts[0]
    assert "招标文件.pdf" in prompt, "规划器必须知道有文件可读"
    assert "[本会话已上传文件" in prompt


def test_node_without_doc_context_does_not_add_file_hint():
    """没带文件的会话不得凭空出现文件清单（否则规划器会去读不存在的文件）。"""
    llm = _CapturePlanLLM()
    node = build_deep_agent_node(llm=llm, tools=[_kb_tool()], reflect=False)

    node({"messages": [HumanMessage(content="政府采购适用什么法律？")]})

    assert "[本会话已上传文件" not in llm.prompts[0]


# ── 观察摘要预算按工具区分（实测缺陷：read_file 的事实被截在句中）──────


def _chunk_series(n: int, chars: int = 900):
    return {"chunks": [
        {"content": f"第{i}章内容。" + "填充文字" * (chars // 4),
         "doc_name": "标书.pdf", "chapter": f"第{i}章"}
        for i in range(1, n + 1)
    ]}


def test_default_budget_truncates_fact_inside_chunk():
    """锁住缺陷本身：默认预算（120 字/块）会把句中事实截断。

    实测原型：第五章「设备质保期：自验收合格之日起五年」被截在「自验收合格之」，
    agent 于是回答"文件中未提及质保期年限"——**检索对了，但答案送不到 LLM 面前**。
    """
    from agent.nodes.deep_agent import _summarize_result

    content = ("交货期：合同签订之日起九十日历天内完成供货、安装与调试。"
               "交货地点：某市第一人民医院医学影像科。"
               "安装调试完成后，由采购人组织验收，验收标准执行国家医疗器械相关标准及投标文件承诺。"
               "验收不合格的，投标人应当在十五日内完成整改并申请复验。"
               "设备质保期：自验收合格之日起五年（含球管等易损件）。")
    result = {"chunks": [{"content": content, "doc_name": "d.pdf", "chapter": "第五章"}]}

    assert "设备质保期：自验收合格之日起五年" not in _summarize_result(result)


def test_read_file_budget_keeps_fact_past_120_chars():
    """read_file 的预算必须保住句中事实（否则 agent 检索对了也答不出）。"""
    from agent.nodes.deep_agent import _summarize_result

    content = ("交货期：合同签订之日起九十日历天内完成供货、安装与调试。"
               "交货地点：某市第一人民医院医学影像科。"
               "安装调试完成后，由采购人组织验收，验收标准执行国家医疗器械相关标准及投标文件承诺。"
               "验收不合格的，投标人应当在十五日内完成整改并申请复验。"
               "设备质保期：自验收合格之日起五年（含球管等易损件）。")
    result = {"chunks": [{"content": content, "doc_name": "d.pdf", "chapter": "第五章"}]}

    out = _summarize_result(result, "read_file")

    assert "设备质保期：自验收合格之日起五年" in out


def test_read_file_budget_covers_more_chunks():
    """默认只取前 3 块，read_file 取前 5 块（read_file 一次返回 6 块）。"""
    from agent.nodes.deep_agent import _summarize_result

    result = _chunk_series(6)

    default_out = _summarize_result(result)
    read_file_out = _summarize_result(result, "read_file")

    assert "第3章" in default_out and "第4章" not in default_out
    assert "第5章" in read_file_out and "第6章" not in read_file_out


def test_read_file_budget_total_cap():
    """总长上限 4000（默认 400），且超限以省略号收尾。"""
    from agent.nodes.deep_agent import _summarize_result

    long_out = _summarize_result(_chunk_series(5, chars=1200), "read_file")

    assert long_out.endswith("…")
    assert len(long_out) <= 4001


def test_kb_and_price_observation_budget_unchanged():
    """回归：kb_search / price_query 不受影响（避免无差别抬高 token 成本）。"""
    from agent.nodes.deep_agent import _summarize_result

    kb = {"chunks": [{"content": "招标方式包括公开招标和邀请招标。" + "甲" * 300,
                      "doc_name": "招标投标法", "chapter": "第一章"}]}
    assert _summarize_result(kb, "kb_search_tool") == _summarize_result(kb)
    assert len(_summarize_result(kb)) <= 401

    price = {"records": [{"project": "某项目"}]}
    assert _summarize_result(price, "price_query") == _summarize_result(price)

    assert _summarize_result({"chunks": []}, "read_file") == ""


# ── 反思解析：从 JSON 改为标记行 + 正文（Phase G-8 实测缺陷）──────────


def test_parse_reflection_handles_untrusted_prose():
    """实测失败原型：修订回答是长中文散文、含未转义引号与真实换行。

    塞进 JSON 字符串必然非法（`Expecting ',' delimiter`）→ 过去只能"采纳草稿"，
    等于**答案没被审校**。标记格式下长文本不进 JSON，从结构上消除该失败。
    """
    from agent.nodes.deep_agent import REFLECT_ANSWER_SEP, _parse_reflection

    text = (
        "VERDICT: revised\nNOTES: 缺少法律依据\n" + REFLECT_ANSWER_SEP + "\n"
        '根据《招标投标法》第三十五条"中标人应当依法签订合同"，\n'
        '该标书的约定"不符合"要求。\n\n要点：\n1. 比例约 6.2%\n2. 上限 2%'
    )

    r = _parse_reflection(text)

    assert r.verdict == "revised"
    assert r.notes == "缺少法律依据"
    assert "中标人应当依法签订合同" in r.revised_answer
    assert "\n" in r.revised_answer          # 换行必须原样保留
    assert '"' in r.revised_answer


def test_parse_reflection_adequate_and_gap_have_no_answer():
    from agent.nodes.deep_agent import _parse_reflection

    adequate = _parse_reflection("VERDICT: adequate\nNOTES: 草稿准确")
    assert adequate.verdict == "adequate" and adequate.revised_answer is None

    gap = _parse_reflection("VERDICT: gap\nNOTES: 观察不足")
    assert gap.verdict == "gap" and gap.revised_answer is None


def test_parse_reflection_tolerates_preamble_and_case():
    from agent.nodes.deep_agent import REFLECT_ANSWER_SEP, _parse_reflection

    r = _parse_reflection(f"好的，我来审校。\n\nverdict：REVISED\nnotes：x\n{REFLECT_ANSWER_SEP}\n修订内容")

    assert r.verdict == "revised" and r.revised_answer == "修订内容"


def test_parse_reflection_still_accepts_legacy_json():
    """向后兼容：模型忽略格式要求、仍输出 JSON 时不能失效。"""
    from agent.nodes.deep_agent import _parse_reflection

    r = _parse_reflection(
        '{"verdict": "revised", "notes": "遗漏依据", "revised_answer": "修订后的完整回答。"}'
    )

    assert r.verdict == "revised" and r.revised_answer == "修订后的完整回答。"


def test_parse_reflection_returns_none_for_garbage():
    from agent.nodes.deep_agent import _parse_reflection

    assert _parse_reflection("完全不是格式") is None
    assert _parse_reflection("") is None


def test_reflect_uses_marker_format_end_to_end():
    """端到端：审校输出走标记格式 → 修订版成为终答（不再出现"采纳草稿"）。"""
    from agent.nodes.deep_agent import REFLECT_ANSWER_SEP

    class _MarkerReflectLLM(_FakeDeepLLM):
        def invoke(self, messages):
            prompt = messages[0].content
            if "深度规划" in prompt:
                return AIMessage(content='[{"objective": "查", "tool": "kb_search_tool", "query": "q"}]')
            if "审校" in prompt:
                return AIMessage(content=(
                    "VERDICT: revised\nNOTES: 补引用\n" + REFLECT_ANSWER_SEP + "\n"
                    '修订版：依据《招标投标法》第三十五条，"应"依法签约。'
                ))
            return AIMessage(content="")

    out = run_deep_agent("q", llm=_MarkerReflectLLM(plan_json="[]", draft="草稿。"),
                         tools=[_kb_tool()])

    assert out["trace"]["reflection"]["verdict"] == "revised"
    assert out["answer"].startswith("修订版")
    assert "依法签约" in out["answer"]


def test_registered_node_entry_builds(monkeypatch):
    """回归：经 registry 接线的节点入口原先**没传 llm_factory** → 一执行就抛
    `ValueError: llm 与 llm_factory 至少提供一个`，被 `_with_fallback` 吞成降级
    话术，因 `enabled=False` 长期未暴露（端到端验收才抓到）。
    """
    import agent.nodes.deep_agent as da
    from agent.nodes.base import get_node_spec

    monkeypatch.setattr(da, "_slot_llm_factory", lambda: object())
    da.reset_deep_agent()
    try:
        node = da._get_deep_agent_node()
        assert callable(node)
        # registry 登记的 entry 就是它（保证测的是生产那条路径）
        assert get_node_spec("deep_agent").entry is da.node_deep_agent
    finally:
        da.reset_deep_agent()


# ── 引用核算的来源标识（可核验性）──────────────────────────────────


def test_extract_sources_from_chunks_and_records():
    """来源标识供「引用覆盖率」核算：chunks 取 doc_name｜chapter，records 取主体名。"""
    from agent.nodes.deep_agent import _extract_sources

    got = _extract_sources({"chunks": [
        {"content": "x", "doc_name": "招标投标法实施条例", "chapter": "第四十七条"},
        {"content": "y", "doc_name": "某标书.pdf", "chapter": "招标文件 > 第三章"},
    ]})
    assert got == ["招标投标法实施条例｜第四十七条", "某标书.pdf｜招标文件 > 第三章"]

    recs = _extract_sources({"records": [{"project_name": "智慧园区项目"}]})
    assert recs == ["智慧园区项目"]

    assert _extract_sources({"chunks": []}) == []
    assert _extract_sources("不是 dict") == []


def test_step_record_carries_sources():
    """轨迹要带上来源，否则无法核算"答案引用的东西是否真的检索到了"。"""
    llm = _FakeDeepLLM(
        plan_json='[{"objective": "查法规", "tool": "kb_search_tool", "query": "履约担保"}]',
        draft="依据《招标投标法实施条例》，招标人应提供工程款支付担保。",
    )

    out = run_deep_agent("q", llm=llm, tools=[_kb_tool()])

    src = out["trace"]["steps"][0]["sources"]
    assert src and "招标投标法" in src[0]
