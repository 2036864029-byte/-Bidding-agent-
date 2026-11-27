"""channels.feishu.render — /chat 响应 → 飞书文本 / 卡片消息。"""

from typing import get_args

import pytest

from channels.feishu.render import render_card_reply, render_text_reply
from core.protocol.reply import ReplyType

# ── render_text_reply（错误/降级文本）─────────────────────────────


def test_text_normal_answer_passthrough():
    text = render_text_reply({"answer": "招标方式包括公开招标等。", "reply_type": "chat"})
    assert "公开招标" in text


def test_text_error_code_maps_to_friendly_text():
    text = render_text_reply({"answer": "", "reply_type": "fallback", "error_code": "sys_2003"})
    assert "频繁" in text
    assert "sys_2003" not in text


def test_text_unknown_error_code_keeps_code():
    text = render_text_reply({"answer": "", "reply_type": "fallback", "error_code": "biz_9999"})
    assert "biz_9999" in text


def test_text_empty_response_placeholder():
    assert "没有可显示的内容" in render_text_reply({"answer": "", "reply_type": "chat"})


# ── render_card_reply（成功回答 → 卡片）───────────────────────────


def _card(resp):
    return render_card_reply(resp)


def test_card_answer_is_markdown_element():
    card = _card({"answer": "招标方式包括**公开招标**。", "reply_type": "chat"})
    assert card["config"]["wide_screen_mode"] is True
    assert card["elements"][0] == {
        "tag": "markdown", "content": "招标方式包括**公开招标**。"
    }


def test_card_clarify_lists_candidates_as_text():
    resp = {
        "answer": "您想问的是哪一种？",
        "reply_type": "clarify",
        "data": {"candidates": ["公开招标流程", "邀请招标流程"]},
    }
    card = _card(resp)
    text_el = [e for e in card["elements"] if e.get("tag") == "div"][0]["text"]
    assert "1. 公开招标流程" in text_el["content"]
    assert "2. 邀请招标流程" in text_el["content"]


def test_card_error_code_is_markdown_card():
    card = _card({"answer": "", "reply_type": "fallback", "error_code": "sys_2004"})
    assert card["elements"][0]["tag"] == "markdown"
    assert "繁忙" in card["elements"][0]["content"]


def test_card_empty_response_placeholder():
    card = _card({"answer": "", "reply_type": "chat"})
    assert card["elements"][0]["text"]["content"] == "（没有可显示的内容）"


@pytest.mark.parametrize("reply_type", list(get_args(ReplyType)))
def test_all_reply_types_render_card(reply_type):
    """未来新增 reply_type 时，卡片渲染至少不炸。"""
    resp = {
        "answer": "内容",
        "reply_type": reply_type,
        "citations": [{"context_index": 1, "doc_name": "d", "text": "x"}],
        "data": {"candidates": ["甲"]},
    }
    assert render_card_reply(resp)["elements"]


# ── P2：header / 原生 table / 思维链 / 状态 note / 折叠引用 ────────


def test_kb_answer_has_no_header_but_others_do():
    """kb_answer 顶格渲染（用户反馈 09-25）：无 header；其它分支保留。"""
    kb = _card({"answer": "x", "reply_type": "kb_answer"})
    assert "header" not in kb
    chat = _card({"answer": "x", "reply_type": "chat"})
    assert chat["header"]["title"] == {"tag": "plain_text", "content": "💬 对话"}
    assert chat["header"]["template"] == "grey"


def test_card_latency_note_when_top_level_present():
    card = _card({"answer": "x", "reply_type": "chat", "latency_ms": 2300})
    note = [e for e in card["elements"] if e.get("tag") == "note"][-1]
    assert "2.3s" in note["elements"][0]["content"]


def test_card_kb_answer_citations_in_collapsible_panel():
    resp = {
        "answer": "依据《招标投标法》…",
        "reply_type": "kb_answer",
        "citations": [
            {"context_index": 1, "doc_name": "招标投标法.md", "chapter": "总则", "text": "原文片段"}
        ],
    }
    card = _card(resp)
    tags = [e.get("tag") for e in card["elements"]]
    assert tags == ["markdown", "collapsible_panel"]
    panel = card["elements"][1]
    assert panel["expanded"] is False                     # 默认收起
    assert panel["header"]["title"] == {"tag": "plain_text", "content": "📚 引用来源（1 条）"}
    assert panel.get("background_color") == "grey"        # 灰色底色块（色块区分）
    inner = panel["elements"][0]["content"]
    assert "【来源1】招标投标法.md" in inner
    assert "总则" in inner
    assert "原文片段" in inner
    # 面板标题已带条数，内部不再重复"共 N 条"
    assert "共 1 条" not in inner


def test_card_kb_answer_citations_fallback_hr_when_disabled(monkeypatch):
    """_CITATIONS_COLLAPSIBLE=False → 回退 hr + 引用块（兼容低版本客户端）。"""
    import channels.feishu.render as rnd

    monkeypatch.setattr(rnd, "_CITATIONS_COLLAPSIBLE", False)
    resp = {
        "answer": "依据《招标投标法》…",
        "reply_type": "kb_answer",
        "citations": [{"context_index": 1, "doc_name": "d", "text": "x"}],
    }
    tags = [e.get("tag") for e in _card(resp)["elements"]]
    assert tags == ["markdown", "hr", "div"]


def _price_resp(records, columns, answer=""):
    return {
        "answer": answer,
        "reply_type": "price_records",
        "data": {"records": records, "columns": columns},
    }


def test_price_records_single_record_keeps_narrative_no_table():
    records = [{"project_name": "A项目", "winning_amount": 12345.6}]
    columns = [
        {"key": "project_name", "label": "项目名称"},
        {"key": "winning_amount", "label": "中标金额"},
    ]
    card = _card(
        _price_resp(
            records, columns, "项目「A项目」由 X 采购，于 2024-05-01 确定中标结果。"
        )
    )
    assert "table" not in [e.get("tag") for e in card["elements"]]
    assert card["elements"][0]["tag"] == "markdown"


def test_price_records_multi_builds_native_table():
    records = [
        {"winning_date": "2024-05-01", "project_name": "A项目", "winning_amount": 1234567.89},
        {"winning_date": "2024-06-01", "project_name": "B项目", "winning_amount": 500000},
    ]
    columns = [
        {"key": "winning_date", "label": "中标日期"},
        {"key": "project_name", "label": "项目名称"},
        {"key": "winning_amount", "label": "中标金额"},
    ]
    answer = (
        "根据条件共匹配到 2 个相关项目，列表如下：\n\n"
        "1 [X] A项目 | 采购人甲 | 中标 乙 | 2024-05-01\n"
        "2 [Y] B项目 | 采购人丙 | 中标 丁 | 2024-06-01\n\n"
        "（数据来源：ztb_clean.bid_project）"
    )
    card = _card(_price_resp(records, columns, answer))
    table = [e for e in card["elements"] if e.get("tag") == "table"][0]
    # 列顺序 = columns 顺序，行键是机器名
    assert [c["name"] for c in table["columns"]] == [
        "winning_date", "project_name", "winning_amount",
    ]
    amount_col = table["columns"][2]
    assert amount_col["data_type"] == "number"
    assert amount_col["format"] == {"symbol": "¥", "precision": 2, "separator": True}
    assert table["rows"][0]["winning_amount"] == 1234567.89
    assert table["rows"][0]["project_name"] == "A项目"
    assert table["page_size"] == 5
    # answer 里的竖线列表行已被剥掉（表格承载），正文只留引导句 + 结语
    bodies = [e["content"] for e in card["elements"] if e.get("tag") == "markdown"]
    assert bodies and "匹配到 2 个相关项目" in bodies[0]
    assert "采购人甲" not in bodies[0]
    assert "数据来源" in bodies[0]


def test_price_records_empty_records_no_table():
    card = _card(_price_resp([], [{"key": "a", "label": "A"}]))
    assert "table" not in [e.get("tag") for e in card["elements"]]


def test_price_records_truncation_note_when_over_cap():
    records = [{"project_name": f"项目{i}", "winning_amount": i} for i in range(60)]
    columns = [
        {"key": "project_name", "label": "项目名称"},
        {"key": "winning_amount", "label": "中标金额"},
    ]
    card = _card(_price_resp(records, columns, "匹配到 60 个相关项目…"))
    table = [e for e in card["elements"] if e.get("tag") == "table"][0]
    assert len(table["rows"]) == 50
    assert table["page_size"] == 10
    notes = [e for e in card["elements"] if e.get("tag") == "note"]
    assert any("前 50 条" in n["elements"][0]["content"] for n in notes)


def test_price_records_amount_column_falls_back_to_text_when_not_numeric():
    columns = [{"key": "winning_amount", "label": "中标金额"}]
    records = [{"winning_amount": "未提供"}, {"winning_amount": "暂未查询到"}]
    card = _card(_price_resp(records, columns, "匹配到 2 个…"))
    table = [e for e in card["elements"] if e.get("tag") == "table"][0]
    assert table["columns"][0]["data_type"] == "text"


def test_deep_agent_trace_note():
    data = {
        "trace": {
            "plan": [{"id": 1, "objective": "x", "tool": "kb_search"}],
            "steps": [
                {
                    "step_id": 1, "tool": "kb_search", "query": "招标方式",
                    "status": "ok", "records_count": 4,
                },
                {
                    "step_id": 2, "tool": "price_query", "query": "中标",
                    "status": "empty", "records_count": 0,
                },
            ],
            "rounds": 2,
            "mode": "plan_execute_reflect",
            "reflection": {"verdict": "adequate", "notes": ""},
        }
    }
    card = _card({"answer": "综合答案", "reply_type": "deep_agent", "data": data})
    note = [e for e in card["elements"] if e.get("tag") == "note"][-1]
    content = note["elements"][0]["content"]
    assert "思维链" in content
    assert "kb_search" in content
    assert "规划 1 步 / 执行 2 步" in content
    assert "反思 adequate" in content


def test_doc_qa_status_note():
    card = _card({
        "answer": "答案",
        "reply_type": "doc_qa",
        "data": {"status": "success", "sources": ["a", "b"], "mode": "retrieval"},
    })
    note = [e for e in card["elements"] if e.get("tag") == "note"][-1]
    assert "已回答 · 2 个来源" in note["elements"][0]["content"]


def test_doc_qa_no_doc_status_note():
    card = _card({"answer": "", "reply_type": "doc_qa", "data": {"status": "no_doc_attached"}})
    note = [e for e in card["elements"] if e.get("tag") == "note"][-1]
    assert "未检测到已上传的文档" in note["elements"][0]["content"]
