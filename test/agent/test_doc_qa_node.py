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
    """可组合的可运行替身：__or__ 返回自身，invoke 记录入参返回固定答案。"""

    def __init__(self, answer: str = "根据文档内容，该标书满足要求。") -> None:
        self.answer = answer
        self.inputs = None

    def __or__(self, other):
        return self

    def invoke(self, inputs):
        self.inputs = inputs
        return self.answer


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
