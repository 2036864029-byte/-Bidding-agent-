"""knowledge_qa 子图（方案 Phase 6 / F-1）离线测试。

不触碰网络：kb_search / _get_llm 打桩（与 test/test_knowledge_qa_tool.py 同款夹具）。
覆盖：
  1. 子图能力探针：作父图节点时 qa_* 独有键不泄漏、messages/business_result 合并回父图。
  2. 行为等价：子图终态 vs chain.answer_knowledge 在相同 fixtures 上逐键一致（F-1 门禁）。
  3. 出口矩阵：拒答 / 检索未就绪(RuntimeError) / 空消息 / 正常生成。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph

import agent.nodes.knowledge_qa.chain as chain_mod
import agent.nodes.knowledge_qa.subgraph as subgraph_mod
from agent.nodes.knowledge_qa.subgraph import (
    KB_MAX_ATTEMPTS,
    _REFUSAL_ANSWER,
    build_knowledge_qa_subgraph,
)
from agent.state import AgentState
from public_kb.guardrails.citations_models import Citation
from public_kb.tools import Chunk, RetrievalResult


# ── 夹具（与 test_knowledge_qa_tool 同构）─────────────────────

def _triple(n: int = 1) -> RetrievalResult:
    chunks = [
        Chunk(content="招标方式分为公开招标和邀请招标。", chunk_id=1, chunk_uid="u1",
              doc_name="招标投标法", chapter="第一章", chunk_index=0, score=0.9)
    ][:n]
    citations = [
        Citation(context_index=1, chunk_id=1, chunk_uid="u1",
                 doc_name="招标投标法", chapter="第一章", chunk_index=0,
                 text="招标方式分为公开招标和邀请招标。", score=0.9)
    ][:n]
    return RetrievalResult(
        chunks=chunks,
        citations_meta=citations,
        rendered_context="[来源1] 文档: 招标投标法 | 章节: 第一章\n招标方式分为公开招标和邀请招标。",
        recall_trace={"mode": "fake"},
    )


def _llm_like(content: str):
    return RunnableLambda(lambda messages: AIMessage(content=content))


def _patch_tool_path(monkeypatch, *, retrieval, answer):
    """同时打桩 chain 与 subgraph 的共享符号（二者各持独立绑定，须分别替换）。"""
    monkeypatch.setattr(chain_mod, "kb_search", lambda q: retrieval)
    monkeypatch.setattr(chain_mod, "_get_llm", lambda: _llm_like(answer))
    monkeypatch.setattr(subgraph_mod, "kb_search", lambda q: retrieval)
    monkeypatch.setattr(subgraph_mod, "_get_llm", lambda: _llm_like(answer))


# ── 1. 子图能力探针：父图节点 + 命名空间隔离 ──────────────────

def test_subgraph_as_parent_node_isolates_internal_keys(monkeypatch):
    """作父图节点：qa_* 独有键不泄漏进父图，共享通道合并回父图。"""
    _patch_tool_path(monkeypatch, retrieval=_triple(1),
                     answer="招标方式包括公开招标和邀请招标。【来源1】")
    sub = build_knowledge_qa_subgraph()

    parent = StateGraph(AgentState)
    parent.add_node("knowledge_qa", sub)
    parent.set_entry_point("knowledge_qa")
    parent.add_edge("knowledge_qa", END)

    out = parent.compile().invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})

    leaked = [k for k in out if k.startswith("qa_")]
    assert leaked == []
    assert out["business_result"]["branch"] == "knowledge_qa"
    assert out["messages"][-1].content == out["business_result"]["answer"]


# ── 2. 行为等价：子图 vs chain（F-1 门禁）─────────────────────

def test_subgraph_equals_chain_full_triple(monkeypatch):
    """正常生成路径：终态与 chain.answer_knowledge 逐键一致。"""
    _patch_tool_path(monkeypatch, retrieval=_triple(1),
                     answer="招标方式包括公开招标和邀请招标。【来源1】")
    chain_out = chain_mod.answer_knowledge("招标方式有哪些？")
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})
    biz = state_out["business_result"]

    assert biz["answer"] == chain_out["answer"]
    assert biz["data"]["sources"] == chain_out["sources"]
    assert biz["data"]["citations"] == chain_out["citations"]
    assert biz["data"]["citation_validation"] == chain_out["citation_validation"]
    assert biz["data"]["retrieval_diagnostics"] == chain_out["retrieval_diagnostics"]
    assert state_out["messages"][-1].content == chain_out["answer"]


def test_subgraph_equals_chain_refusal(monkeypatch):
    """空检索拒答路径：终态与 chain 逐键一致（含拒答护栏报告）。"""
    _patch_tool_path(monkeypatch, retrieval=RetrievalResult(), answer="不应触发生成")
    chain_out = chain_mod.answer_knowledge("查无此条")
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="查无此条")]})
    biz = state_out["business_result"]

    assert biz["answer"] == chain_out["answer"] == _REFUSAL_ANSWER
    assert biz["data"]["sources"] == chain_out["sources"] == []
    assert biz["data"]["citations"] == chain_out["citations"] == []
    assert biz["data"]["citation_validation"] == chain_out["citation_validation"]
    assert biz["data"]["retrieval_diagnostics"] == chain_out["retrieval_diagnostics"]


# ── 3. 出口矩阵 ──────────────────────────────────────────────

def test_subgraph_retrieval_runtime_error_friendly(monkeypatch):
    """kb_search 抛 RuntimeError → 知识库未初始化引导（同 node 现状）。"""
    def boom(q):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(subgraph_mod, "kb_search", boom)
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})

    assert "知识库尚未初始化" in state_out["business_result"]["answer"]
    assert state_out["business_result"]["data"]["error"] == "milvus down"
    assert state_out["messages"][-1].content == "知识库尚未初始化，请联系管理员。"


def test_subgraph_empty_messages_friendly():
    """空消息守卫：与 node._run_tool_node 一致（不写 messages，仅 business_result）。"""
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": []})

    assert "没有收到您的问题" in state_out["business_result"]["answer"]
    assert "messages" not in state_out or len(state_out.get("messages", [])) == 0


# ── 4. 护栏失败有界重试（F-3）─────────────────────────────────
#
# 注：默认校验配置下 R7（严格模式）关闭、R6 开启——「无【来源N】标注」的回答会
# 通过校验（R1~R5 只看 citations 结构一致性）；真正触发失败的是「引用不存在的
# 来源」（R6 幻影来源），故本组测试用【来源9】构造首轮失败、【来源1】构造重试通过。

_FAIL_ANSWER = "根据【来源9】，招标方式分为公开招标和邀请招标。"  # 幻影来源 → R6 失败
_PASS_ANSWER = "根据【来源1】，招标方式分为公开招标和邀请招标。"  # 合法引用 → 通过


def _patch_retrieval(monkeypatch):
    monkeypatch.setattr(subgraph_mod, "kb_search", lambda q: _triple(1))


def _patch_llm_sequence(monkeypatch, answers):
    """按调用顺序返回不同回答的假 LLM；并记录每次 prompt 输入。"""
    seen_prompts = []

    def fake_llm():
        def _runnable(prompt_value):
            # prompt | llm 时 llm 收到 MessagePromptValue（pydantic），取 .messages
            msgs = getattr(prompt_value, "messages", prompt_value)
            seen_prompts.append("\n".join(m.content for m in msgs))
            content = answers[min(len(seen_prompts) - 1, len(answers) - 1)]
            return AIMessage(content=content)
        return RunnableLambda(_runnable)

    monkeypatch.setattr(subgraph_mod, "_get_llm", fake_llm)
    return seen_prompts


def test_subgraph_guardrail_retry_success(monkeypatch):
    """首轮护栏失败 → 反馈注入重生成 → 通过（总 2 次生成，终态 all_passed）。"""
    _patch_retrieval(monkeypatch)
    _patch_llm_sequence(monkeypatch, [_FAIL_ANSWER, _PASS_ANSWER])
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})
    biz = state_out["business_result"]

    assert biz["answer"] == _PASS_ANSWER
    assert biz["data"]["citation_validation"]["all_passed"] is True
    assert state_out["qa_attempts"] == 1


def test_subgraph_retry_feedback_injected_into_prompt(monkeypatch):
    """重试轮 prompt 必须携带护栏失败原因（修改要求块）。"""
    _patch_retrieval(monkeypatch)
    seen = _patch_llm_sequence(monkeypatch, [_FAIL_ANSWER, _PASS_ANSWER])
    sub = build_knowledge_qa_subgraph()
    sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})

    assert len(seen) == 2
    assert "修改要求" in seen[1]            # 反馈注入
    assert "R6" in seen[1] or "无效标记" in seen[1]


def test_subgraph_guardrail_retry_exhausted_returns_report(monkeypatch):
    """重试耗尽（仍失败）：返回末轮答案 + 校验报告（现状语义），不再无限循环。"""
    _patch_retrieval(monkeypatch)
    _patch_llm_sequence(monkeypatch, ["第1轮" + _FAIL_ANSWER, "第2轮" + _FAIL_ANSWER])
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})
    biz = state_out["business_result"]

    assert state_out["qa_attempts"] == KB_MAX_ATTEMPTS - 1
    assert biz["answer"] == "第2轮" + _FAIL_ANSWER
    assert biz["data"]["citation_validation"]["all_passed"] is False


def test_subgraph_guardrail_retry_disabled(monkeypatch):
    """kb_guardrail_retry=false：失败一次即返回，不重试（P6 前现状语义）。"""
    _patch_retrieval(monkeypatch)
    _patch_llm_sequence(monkeypatch, [_FAIL_ANSWER])
    settings = subgraph_mod._get_settings()
    monkeypatch.setattr(settings, "kb_guardrail_retry", False)

    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="招标方式有哪些？")]})
    biz = state_out["business_result"]

    assert biz["answer"] == _FAIL_ANSWER
    assert "qa_attempts" not in state_out
    assert biz["data"]["citation_validation"]["all_passed"] is False


def test_subgraph_refusal_never_triggers_retry(monkeypatch):
    """空检索拒答路径：不触发生成，也就无重试（护栏拒答报告 is_refusal=True）。"""
    monkeypatch.setattr(subgraph_mod, "kb_search", lambda q: RetrievalResult())
    sub = build_knowledge_qa_subgraph()
    state_out = sub.invoke({"messages": [HumanMessage(content="查无此条")]})
    biz = state_out["business_result"]

    assert biz["answer"] == _REFUSAL_ANSWER
    assert biz["data"]["citation_validation"]["is_refusal"] is True
    assert "qa_attempts" not in state_out
