"""叙述正文截断（2026-09-16）：中标项目很多时正文只列前 N 条，其余省略指向表格。

背景：主对话问"XX公司中标了哪些项目？"返回 20 条记录时，正文会刷出 20 行编号列表
+ 下方同样的表格，冗余且长。改为正文只列 `NARRATIVE_MAX_ITEMS`（5）条，
附"其余见下表"，完整列表由表格呈现（表格上限 `price_display_top_k`，默认 30）。
"""

from __future__ import annotations

from price_engine.rendering.answer_templates import (
    NARRATIVE_MAX_ITEMS,
    render_answer,
)


def _records(n: int) -> list[dict]:
    return [
        {
            "project_name": f"项目{i}",
            "project_number": f"ZB{i}",
            "winning_date": f"2026-01-{i + 1:02d}",
            "purchaser": "某采购人",
            "successful_bidder": "某公司",
            "winning_amount": "100000",
        }
        for i in range(n)
    ]


def test_multi_truncates_to_max_items_with_omission_note():
    answer = render_answer("bidder_query", _records(8), entity="某公司")

    # 头部总数仍是全量（8 条）
    assert "共中标 8 个项目" in answer
    # 只列前 5 条（NARRATIVE_MAX_ITEMS）
    for i in range(NARRATIVE_MAX_ITEMS):
        assert f"项目{i}" in answer
    assert "项目5" not in answer
    assert "项目7" not in answer
    # 省略说明指向下方表格
    assert "其余 3 条见下表，共 8 条记录" in answer


def test_multi_within_limit_lists_all():
    answer = render_answer("bidder_query", _records(3), entity="某公司")
    assert "共中标 3 个项目" in answer
    assert "项目0" in answer and "项目2" in answer
    assert "见下表" not in answer  # 未超限不加省略说明


def test_truncation_applies_to_other_multi_templates():
    # purchaser_query 同样受截断保护
    answer = render_answer("purchaser_query", _records(7), entity="某采购人")
    assert "共发布 7 个项目" in answer
    assert "项目0" in answer
    assert "项目6" not in answer
    assert "其余 2 条见下表" in answer
