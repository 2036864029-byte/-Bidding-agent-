"""doc_qa 节点测试（9.3 Track D / D-2）— mock llm，不触网络。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import agent.nodes.doc_qa as doc_qa
from agent import AgentGraph
from agent.nodes.doc_qa import DOC_CONTEXT_MARKER, extract_doc_context, node_doc_qa


def _context_state(question="帮我分析这份标书", name="标书.pdf", body="第一章 采购需求与技术要求。"):
    sys_msg = SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：{name}]\n{body}")
    return {"messages": [sys_msg, HumanMessage(content=question)]}


class FakeRunnable:
    """可组合的可运行替身：__or__ 返回自身，invoke/stream 记录入参返回固定答案。"""

    def __init__(self, answer: str = "根据文档内容，该标书满足要求。") -> None:
        self.answer = answer
        self.inputs = None

    def __or__(self, other):
        return self

    def invoke(self, inputs):
        self.inputs = inputs
        return self.answer

    def stream(self, inputs):
        self.inputs = inputs
        yield self.answer


def test_no_doc_returns_guidance():
    result = node_doc_qa({"messages": [HumanMessage(content="帮我分析这个文档")]})
    br = result["business_result"]
    assert br["branch"] == "doc_qa"
    assert br["data"]["status"] == "no_doc_attached"
    assert br["data"]["attached_docs"] == []
    assert isinstance(result["messages"][0], AIMessage)


def test_success_generates_from_context(monkeypatch):
    fake = FakeRunnable()
    monkeypatch.setattr(doc_qa, "_get_llm", lambda: fake)
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: fake)

    result = node_doc_qa(_context_state())

    br = result["business_result"]
    assert br["data"]["status"] == "success"
    assert br["data"]["attached_docs"] == ["标书.pdf"]
    assert br["answer"] == fake.answer
    assert fake.inputs["question"] == "帮我分析这份标书"
    assert "第一章 采购需求与技术要求。" in fake.inputs["context"]


def test_generation_failure_returns_failed_status(monkeypatch):
    class Failing:
        def __or__(self, other):
            return self

        def invoke(self, inputs):
            raise RuntimeError("mock llm 失败")

        def stream(self, inputs):
            raise RuntimeError("mock llm 失败")

    monkeypatch.setattr(doc_qa, "_get_llm", lambda: Failing())
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: Failing())

    result = node_doc_qa(_context_state())
    br = result["business_result"]
    assert br["data"]["status"] == "failed"
    assert "失败" in br["answer"]


def test_extract_doc_context_parses_names_and_body():
    messages = [
        SystemMessage(
            content=f"{DOC_CONTEXT_MARKER}\n[文件：a.pdf]\nAAA\n[文件：b.docx]\nBBB"
        ),
        HumanMessage(content="q"),
    ]
    names, context = extract_doc_context(messages)
    assert names == ["a.pdf", "b.docx"]
    assert "AAA" in context
    assert "BBB" in context


def test_stale_doc_context_not_reused_across_turns():
    """9.3 验收修复：上一轮注入的文档上下文不得跨轮残留（file_ids 每轮显式传入）。"""
    messages = [
        SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：a.pdf]\nAAA"),
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),  # 本轮未注入文档
    ]
    names, context = extract_doc_context(messages)
    assert names == []
    assert context == ""


def test_extract_doc_context_requires_trailing_human_message():
    # 图中间态（末条非 HumanMessage）不误判
    messages = [SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：a.pdf]\nAAA")]
    names, context = extract_doc_context(messages)
    assert names == []
    assert context == ""


def test_build_input_messages_injects_context():
    messages = AgentGraph._build_input_messages(
        "帮我分析", [("a.pdf", "AAA"), ("b.txt", "BBB")]
    )
    assert isinstance(messages[0], SystemMessage)
    assert DOC_CONTEXT_MARKER in messages[0].content
    assert "[文件：a.pdf]" in messages[0].content
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "帮我分析"


def test_build_input_messages_without_context():
    messages = AgentGraph._build_input_messages("q", None)
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)


# ── B2：路由前置判定（与节点同口径）──────────────────────────


def test_doc_context_kind_matches_extract():
    from agent.nodes.doc_qa import doc_context_kind

    assert doc_context_kind(_context_state()["messages"]) == "explicit"
    assert doc_context_kind([HumanMessage(content="随便问问")]) is None
    # 上一轮的注入不算本轮（与 extract_doc_context 一致）
    assert doc_context_kind([
        SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：a.pdf]\nAAA"),
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
    ]) is None


class _FakeRouterLLM:
    """单图路由用鸭子类型 LLM：固定判 knowledge_qa（模拟"文件被忽略"的旧行为）。"""

    class _Structured:
        def invoke(self, messages):  # noqa: ARG002
            from types import SimpleNamespace

            return SimpleNamespace(intent="knowledge_qa", reason="测试")

    def with_structured_output(self, model):  # noqa: ARG002
        return self._Structured()

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self


def test_single_mode_router_prefers_doc_qa_with_context():
    """单图路径同样受 L0 保护：带了文件就不许被判成别的意图。"""
    from agent.router import build_router_node

    node = build_router_node(_FakeRouterLLM())

    assert node({"messages": _context_state()["messages"]})["router_intent"] == "doc_qa"


def test_single_mode_router_unchanged_without_context():
    from agent.router import build_router_node

    node = build_router_node(_FakeRouterLLM())

    out = node({"messages": [HumanMessage(content="政府采购应适用什么法律？")]})
    assert out["router_intent"] == "knowledge_qa"
