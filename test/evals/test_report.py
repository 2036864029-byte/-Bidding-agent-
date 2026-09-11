"""evals.report — 统一报告装配离线测试（E2-5）。"""

from __future__ import annotations

from evals.report import LAYERS, build_unified_report


def test_build_unified_report_all_layers():
    sections = {layer: f"内容-{layer}" for layer in LAYERS}
    text = build_unified_report(sections, meta={"层数": "5/5"})
    assert "统一评测报告" in text
    assert "层数: 5/5" in text
    for layer in LAYERS:
        assert f"内容-{layer}" in text


def test_missing_layer_marked_not_run():
    text = build_unified_report({"retrieval": "检索层内容"})
    assert "检索层内容" in text
    assert "未运行" in text          # 缺层显式标注


def test_empty_sections_still_valid():
    text = build_unified_report({})
    assert "统一评测报告" in text
    assert text.count("未运行") >= len(LAYERS)
