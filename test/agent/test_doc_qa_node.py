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


# ── C1：检索模式（块标记解析 / 引用来源 / C2 兜底）──────────────────


def _retrieval_body(*blocks, used=None, total=57, max_score=None) -> str:
    """构造 ``agent.doc_context`` 的检索模式注入正文（复用其格式常量，防格式漂移）。"""
    from agent.doc_context import BLOCK_LINE, RETRIEVAL_HEADER

    used = used if used is not None else len(blocks)
    max_score = max_score if max_score is not None else max(b[2] for b in blocks)
    lines = [RETRIEVAL_HEADER.format(used=used, total=total, score=max_score)]
    for seq, (chunk_index, chapter, score, content) in enumerate(blocks, start=1):
        lines.append(
            BLOCK_LINE.format(idx=chunk_index, seq=seq, total=used,
                              chapter=chapter, score=score)
        )
        lines.append(content)
    return "\n".join(lines)


def test_parse_injected_docs_extracts_blocks_and_mode():
    from agent.nodes.doc_qa import MODE_RETRIEVAL, parse_injected_docs

    body = _retrieval_body(
        (12, "第三章 评标办法", 0.87, "评标委员会成员人数为五人。"),
        (40, "第四章 合同条款", 0.61, "合同价款支付方式为分期支付。"),
    )
    docs = parse_injected_docs(f"[文件：招标文件.pdf]\n{body}")

    assert len(docs) == 1
    doc = docs[0]
    assert doc.name == "招标文件.pdf"
    assert doc.mode == MODE_RETRIEVAL
    assert doc.max_score == 0.87
    assert [b.chunk_index for b in doc.blocks] == [12, 40]
    assert [b.chapter for b in doc.blocks] == ["第三章 评标办法", "第四章 合同条款"]
    assert doc.blocks[0].content == "评标委员会成员人数为五人。"
    assert doc.blocks[1].content == "合同价款支付方式为分期支付。"


def test_parse_injected_docs_fulltext_has_no_blocks():
    from agent.nodes.doc_qa import MODE_FULLTEXT, parse_injected_docs

    docs = parse_injected_docs("[文件：a.pdf]\n第一章 内容\n第二章 内容")

    assert len(docs) == 1
    assert docs[0].mode == MODE_FULLTEXT
    assert docs[0].blocks == []


def test_parse_injected_docs_attributes_blocks_per_file():
    """多文件时块必须归属到各自文件（引用里 doc 字段靠这个）。"""
    from agent.nodes.doc_qa import parse_injected_docs

    body = (
        f"[文件：a.pdf]\n{_retrieval_body((1, 'A章', 0.9, 'A 的内容'), total=20)}\n"
        f"[文件：b.pdf]\n{_retrieval_body((7, 'B章', 0.4, 'B 的内容'), total=30)}"
    )
    docs = parse_injected_docs(body)

    assert [d.name for d in docs] == ["a.pdf", "b.pdf"]
    assert [[b.chapter for b in d.blocks] for d in docs] == [["A章"], ["B章"]]


def test_parse_injected_docs_ignores_text_without_file_marker():
    from agent.nodes.doc_qa import parse_injected_docs

    assert parse_injected_docs("没有文件标记的正文") == []


def test_node_retrieval_mode_populates_sources(monkeypatch):
    fake = FakeRunnable()
    monkeypatch.setattr(doc_qa, "_get_llm", lambda: fake)
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: fake)

    result = node_doc_qa(_context_state(body=_retrieval_body(
        (12, "第三章 评标办法", 0.87, "评标委员会成员人数为五人。"),
        (40, "第四章 合同条款", 0.61, "合同价款支付方式为分期支付。"),
    )))

    data = result["business_result"]["data"]
    assert data["status"] == "success"
    assert data["mode"] == "retrieval"
    assert data["attached_docs"] == ["标书.pdf"]
    assert [s["chunk_index"] for s in data["sources"]] == [12, 40]
    assert data["sources"][0]["chapter"] == "第三章 评标办法"
    assert data["sources"][0]["doc"] == "标书.pdf"
    assert data["retrieval"]["blocks"] == 2
    assert data["retrieval"]["notes"] == []
    # 检索模式的块正文必须原样进入 LLM 上下文
    assert "评标委员会成员人数为五人。" in fake.inputs["context"]


def test_node_low_confidence_appends_caveat(monkeypatch):
    """C2：命中相关度不足时明确告知，而不是默默答错。"""
    fake = FakeRunnable()
    monkeypatch.setattr(doc_qa, "_get_llm", lambda: fake)
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: fake)

    result = node_doc_qa(_context_state(body=_retrieval_body(
        (3, "第一章 采购需求", 0.12, "与问题无关的内容。"),
    )))

    data = result["business_result"]["data"]
    note = data["retrieval"]["notes"]
    assert len(note) == 1 and "未检索到" in note[0] and "0.12" in note[0]
    assert result["business_result"]["answer"].endswith(note[0])


def test_node_fulltext_truncation_appends_caveat(monkeypatch):
    """C2：全文模式被截断时明确告知（不静默丢后文）。"""
    fake = FakeRunnable()
    monkeypatch.setattr(doc_qa, "_get_llm", lambda: fake)
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: fake)
    settings = doc_qa._get_settings()

    long_body = "第一章 采购需求。" + "本项目要求如下。" * 2500
    assert len(long_body) > settings.chunk_max_chars * 8

    result = node_doc_qa(_context_state(body=long_body))

    data = result["business_result"]["data"]
    assert data["mode"] == "fulltext"
    assert data["retrieval"]["truncated"] is True
    assert "仅使用了前" in data["retrieval"]["notes"][0]
    assert len(fake.inputs["context"]) == settings.chunk_max_chars * 8


def test_node_failure_keeps_retrieval_payload(monkeypatch):
    """生成失败时 data 仍带 mode/sources/retrieval（输出护栏要 status，前端要引用）。"""

    class Failing(FakeRunnable):
        def stream(self, inputs):
            raise RuntimeError("mock llm 失败")

    monkeypatch.setattr(doc_qa, "_get_llm", lambda: Failing())
    monkeypatch.setattr(doc_qa, "_get_prompt", lambda: Failing())

    result = node_doc_qa(_context_state(body=_retrieval_body(
        (12, "第三章 评标办法", 0.87, "评标委员会成员人数为五人。"),
    )))

    data = result["business_result"]["data"]
    assert data["status"] == "failed"
    assert data["mode"] == "retrieval"
    assert data["sources"][0]["chunk_index"] == 12
    assert "retrieval" in data

