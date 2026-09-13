"""read_file 工具测试（第一梯队第 2 项 / Phase G-6）— 假 embedding，不触网络。

覆盖 Phase G-6 的两条触发路径共用同一判定链：
  - 索引命中 → 秒回
  - 索引未命中 → **现场建索引**后再检索（工具独有的自愈能力）
  - 小文档 → 全文
以及文件名解析（宽松匹配 / 多义不静默挑）、会话与租户隔离、
以及**对 deep_agent 观察摘要契约的符合性**（chunks 形状）。
"""

from __future__ import annotations

import pytest

from agent.doc_context import chunk_record, read_file_as_tool, read_session_file
from core import doc_index, filestore
from core.config.settings import Settings
from core.observability.context import (
    reset_context,
    set_session_id,
    tenant_scoped,
)

#: 基准时间必须贴近当前时刻——FileRecord 有 TTL 校验，用假的 epoch 会被判过期
T0 = filestore.time.time()


@pytest.fixture(autouse=True)
def _isolate_session_context():
    """会话/租户是 contextvar：不隔离会跨用例串（读到上一个用例的文件）。"""
    reset_context()
    yield
    reset_context()


def _markdown(sections: int) -> str:
    parts = ["# 招标文件"]
    for i in range(1, sections + 1):
        parts.append(
            f"## 第{i}章 条款{i}\n"
            f"本条规定了第{i}项具体要求，投标保证金金额为{i}万元，工期要求 {i} 日历天。"
        )
    return "\n".join(parts)


def _put(name: str, markdown: str, chunk_count: int, *, file_id: str, ts: float, session="s1"):
    filestore.put(filestore.FileRecord(
        file_id=file_id, source_name=name, source_format="pdf",
        markdown=markdown, preview=markdown[:40], chunk_count=chunk_count,
        session_id=tenant_scoped(session), created_at=ts,
    ))


@pytest.fixture
def long_doc():
    """长文档（20 块 > 小文档阈值）：索引缺失时需现场建。"""
    _put("大标书.pdf", _markdown(20), 20, file_id="F1", ts=T0)
    return "大标书.pdf"


@pytest.fixture
def small_doc():
    _put("小文件.txt", "# 标题\n一句话。", 1, file_id="F2", ts=T0 + 10)
    return "小文件.txt"


# ── 索引命中 / 未命中现场建索引（Phase G-6 两条路径）──────────────────


def test_read_file_builds_index_on_demand(fake_doc_embeddings, long_doc):
    """上传时未建索引（如向量化失败）→ 工具现场补建（唯一自愈机会）。"""
    set_session_id("s1")
    assert doc_index.load("F1", tenant_scoped("s1")) is None

    out = read_session_file("第7章投标保证金多少？", file_name=long_doc)

    assert out["mode"] == "retrieval"
    assert out["matched"] > 0
    assert "现场建立" in out["note"]
    assert doc_index.load("F1", tenant_scoped("s1")) is not None, "应当已落索引"


def test_read_file_second_call_hits_existing_index(fake_doc_embeddings, long_doc):
    """命中已建索引 → 秒回，不再出现"现场建立"提示。"""
    set_session_id("s1")
    read_session_file("投标保证金", file_name=long_doc)

    out = read_session_file("投标保证金金额", file_name=long_doc)

    assert out["mode"] == "retrieval"
    assert "note" not in out


def test_read_file_fulltext_for_small_doc(fake_doc_embeddings, small_doc):
    """小文档不建索引，直接返回全文（与上下文渲染同一阈值口径）。"""
    set_session_id("s1")

    out = read_session_file("讲了什么", file_name=small_doc)

    assert out["mode"] == "fulltext"
    assert out["chunks"][0]["content"].startswith("# 标题")
    assert out["truncated"] is False


def test_read_file_truncates_oversized_fulltext(fake_doc_embeddings):
    """全文模式按 chunk_max_chars*8 截断并如实告知（C2 口径）。"""
    set_session_id("s1")
    settings = Settings()
    limit = settings.chunk_max_chars * 8
    _put("超长.txt", "第一章 内容。" + "本项目要求如下。" * 2500, 5,
         file_id="FBIG", ts=T0)
    assert len(filestore.get("FBIG").markdown) > limit

    out = read_session_file("问题", file_name="超长.txt")

    assert out["truncated"] is True
    assert len(out["chunks"][0]["content"]) == limit
    assert "仅返回了前" in out["note"]


def test_read_file_reports_low_confidence(fake_doc_embeddings, long_doc):
    """文档里没有该问题的答案 → 明确告知，而不是给出貌似有据的回答。"""
    set_session_id("s1")

    out = read_session_file("今天天气怎么样", file_name=long_doc)

    assert out["low_confidence"] is True
    assert "相关度较低" in out["note"]


def test_read_file_honors_top_k(fake_doc_embeddings, long_doc):
    set_session_id("s1")

    out = read_session_file("投标保证金", file_name=long_doc, top_k=2)

    assert out["matched"] == 2


def test_read_file_zero_top_k_uses_config(fake_doc_embeddings, long_doc):
    """top_k=0 是"用配置值"，不是"取 1 条"（deep_agent 默认参数就是 0）。"""
    set_session_id("s1")
    configured = Settings().doc_index_top_k

    out = read_session_file("投标保证金", file_name=long_doc, top_k=0)

    assert out["matched"] == configured


# ── 文件名解析 ───────────────────────────────────────────────────────


def test_read_file_blank_name_covers_whole_session(fake_doc_embeddings, long_doc, small_doc):
    """留空名 → **全部文件**（调研文档 §8.3 方案 A），不再是"最近上传的那一个"。

    旧行为（取最近）会让多文件问题必然失败：规划器通常不传名字，连续两次调用
    拿回的是同一份文件。多文件的具体断言见文件末尾的「多文件」一节。
    """
    set_session_id("s1")

    out = read_session_file("工期")

    assert {s["file"] for s in out["files_searched"]} == {long_doc, small_doc}
    assert "file" not in out          # 多文件时不再有单一 file 字段


def test_read_file_matches_by_stem(fake_doc_embeddings, long_doc):
    """LLM 常省略扩展名；主名匹配优先于子串（避免误配到"大标书附件"这类）。"""
    set_session_id("s1")
    _put("大标书附件.pdf", "# 附\n内容", 1, file_id="F3", ts=T0 + 99)

    out = read_session_file("投标保证金", file_name="大标书")

    assert out["file"] == "大标书.pdf"


def test_read_file_matches_by_unique_substring(fake_doc_embeddings, long_doc, small_doc):
    set_session_id("s1")

    out = read_session_file("投标保证金", file_name="文件.")

    assert out["file"] == small_doc


def test_read_file_ambiguous_name_does_not_silently_pick(fake_doc_embeddings, small_doc):
    """多义 → 报错并让 LLM 用全名重试（静默挑一个会让 agent 读错文件而不自知）。"""
    set_session_id("s1")
    _put("报价A.pdf", "# A", 1, file_id="FA", ts=T0 + 20)
    _put("报价B.pdf", "# B", 1, file_id="FB", ts=T0 + 30)

    out = read_session_file("问题", file_name="报价")

    assert out["chunks"] == []
    assert "多个" in out["error"]
    assert set(out["available_files"]) >= {"报价A.pdf", "报价B.pdf"}


def test_read_file_unknown_name_lists_available(fake_doc_embeddings, long_doc):
    set_session_id("s1")

    out = read_session_file("问题", file_name="不存在.pdf")

    assert out["chunks"] == []
    assert "未找到" in out["error"]
    assert out["available_files"] == [long_doc]


# ── 会话 / 租户隔离（W0-6 同源要求）─────────────────────────────────


def test_read_file_without_session_context_errors(fake_doc_embeddings, long_doc):
    """CLI / 无会话入口：明确报错，而不是读到任意文件。"""
    reset_context()

    out = read_session_file("问题")

    assert out["chunks"] == []
    assert "没有会话上下文" in out["error"]


def test_read_file_scoped_to_session(fake_doc_embeddings, long_doc):
    """别的会话看不到本会话的文件（单文件跨租户不可读）。"""
    set_session_id("s2")

    out = read_session_file("问题")

    assert out["chunks"] == []
    assert out["available_files"] == []


def test_read_file_cannot_reach_other_session_file_by_name(fake_doc_embeddings, long_doc):
    """点名别的会话的文件也读不到（按会话作用域检索，不是按全局文件名）。"""
    set_session_id("s2")

    out = read_session_file("投标保证金", file_name=long_doc)

    assert out["chunks"] == []
    assert long_doc not in out["available_files"]
    assert out["available_files"] == []


# ── 工具形态 / deep_agent 契约符合性 ─────────────────────────────────


def test_read_file_tool_is_langchain_tool():
    tool = read_file_as_tool()

    assert tool.name == "read_file"
    assert "上传" in tool.description


def test_read_file_conforms_to_deep_agent_observation_contract(fake_doc_embeddings, long_doc):
    """返回的 chunks 形状必须被 deep_agent 现成的观察摘要器/计数吃掉。

    否则轨迹里 `records_count=0`、观察为空 → agent 会以为工具什么都没查到。
    """
    from agent.nodes.deep_agent import _count_records, _summarize_result

    set_session_id("s1")
    out = read_file_as_tool().invoke({"question": "投标保证金金额", "file_name": long_doc})

    assert _count_records(out) > 0
    assert _summarize_result(out)
    assert {"content", "doc_name", "chapter", "chunk_index", "score"} <= set(out["chunks"][0])


# ── 多文件：留空 = 在全部文件内检索（调研文档 §8.3 方案 A）────────────


def _multi_markdown(label: str, fact: str) -> str:
    parts = ["# 招标文件"]
    for i in range(1, 16):
        parts.append(f"## 第{i}章 条款{i}\n{label}第{i}项要求：{fact}（第{i}章）")
    return "\n".join(parts)


@pytest.fixture
def two_docs(fake_doc_embeddings):
    """两份长文档（各 15 块 > 阈值），标题同构、事实不同。"""
    _put("甲文件.pdf", _multi_markdown("甲", "工期为 240 天"), 0, file_id="FA", ts=T0)
    _put("乙文件.pdf", _multi_markdown("乙", "工期为 180 天"), 0, file_id="FB", ts=T0 + 10)
    for fid in ("FA", "FB"):
        rec = filestore.get(fid)
        rec.chunk_count = len(chunk_record(rec, Settings()))
        filestore.put(rec)
    return ["甲文件.pdf", "乙文件.pdf"]


def test_read_file_blank_name_searches_all_files(two_docs):
    """留空不再取"最近上传的那一个"——否则多文件问题必然拿回同一份文件。

    实测原型（multi_file 类 50% 的根因）："只返回了《道路工程》的工期条款，
    未返回《智慧园区》的任何工期信息" —— 两次调用拿回同一份文件。
    """
    set_session_id("s1")

    out = read_session_file("工期是多少天？", file_name="")

    assert {c["doc_name"] for c in out["chunks"]} == {"甲文件.pdf", "乙文件.pdf"}
    assert [s["file"] for s in out["files_searched"]] == ["乙文件.pdf", "甲文件.pdf"]
    assert "已在全部 2 份文件内检索" in out["note"]


def test_read_file_allocates_quota_per_file(two_docs):
    """配额公平分配：不同文档的余弦分不可比，不能全局按分数排序。"""
    set_session_id("s1")

    out = read_session_file("工期是多少天？", top_k=6)

    per = {}
    for c in out["chunks"]:
        per[c["doc_name"]] = per.get(c["doc_name"], 0) + 1
    assert per == {"甲文件.pdf": 3, "乙文件.pdf": 3}


def test_read_file_named_file_still_targets_one(two_docs):
    """指定文件名 → 仍只读那一份（不回归）。"""
    set_session_id("s1")

    out = read_session_file("工期", file_name="甲文件.pdf")

    assert out["file"] == "甲文件.pdf"
    assert {c["doc_name"] for c in out["chunks"]} == {"甲文件.pdf"}
    assert "已在全部" not in (out.get("note") or "")


def test_read_file_single_file_session_unchanged(fake_doc_embeddings):
    """单文件会话留空 → 与原契约一致（不再出现"全部文件"提示）。"""
    _put("单份.pdf", _multi_markdown("单", "工期为 90 天"), 0, file_id="FC", ts=T0)
    rec = filestore.get("FC")
    rec.chunk_count = len(chunk_record(rec, Settings()))
    filestore.put(rec)
    set_session_id("s1")

    out = read_session_file("工期")

    assert out["file"] == "单份.pdf"
    assert "已在全部" not in (out.get("note") or "")
    assert {"file", "mode", "max_score", "low_confidence", "total_chunks"} <= set(out)


def test_read_file_reports_files_searched_with_modes(fake_doc_embeddings):
    """files_searched 逐份给 mode/命中数——多文件时 agent 与评测都要靠它归因。"""
    _put("大.pdf", _multi_markdown("大", "工期为 300 天"), 0, file_id="FD", ts=T0)
    _put("小.txt", "# 小文件\n一句话。", 1, file_id="FE", ts=T0 + 10)
    big = filestore.get("FD")                      # 真实块数决定走检索还是全文
    big.chunk_count = len(chunk_record(big, Settings()))
    filestore.put(big)
    set_session_id("s1")

    out = read_session_file("工期")

    modes = {s["file"]: s["mode"] for s in out["files_searched"]}
    assert modes == {"大.pdf": "retrieval", "小.txt": "fulltext"}
    assert out["mode"] == "retrieval"          # 任一份走检索 → 整体检索模式
    per = {}
    for c in out["chunks"]:
        per[c["doc_name"]] = per.get(c["doc_name"], 0) + 1
    assert per["小.txt"] == 1                  # 小文档全文 = 1 块
    assert per["大.pdf"] == max(2, -(-6 // 2))  # 每份配额 = ceil(top_k / 文件数)
