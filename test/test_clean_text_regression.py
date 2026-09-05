"""Phase A A5 保真回归门禁（test/golden/clean_text_metrics.json 提交基线）。

任何抽取/清洗改动必须过文本级保真比对（对照评估报告指标：页眉残留/页码残留/
正文首行丢失）。主测试书 = 《全书》（A6 策略：主书达标是必要条件，泛化集由
scripts/run_clean_text_metrics.py 全量回归）。

性能：全书 Tier A 逐页 profile/classify/extract 约 3.5 分钟/本，属**慢回归**
——默认快速套件跳过，Phase A 验收时显式跑：
  RUN_CLEAN_TEXT_REGRESSION=1 python -m pytest test/test_clean_text_regression.py -v
另依赖 DATA/raw_data/law_pdf（本机数据，不入仓），PDF 缺失同样跳过。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "DATA" / "raw_data" / "law_pdf"
BASELINE_PATH = ROOT / "test" / "golden" / "clean_text_metrics.json"
QUANSHU = (
    "中华人民共和国招标投标法律法规全书：含相关政策 "
    "(法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)


def _load_baseline() -> dict:
    assert BASELINE_PATH.exists(), f"提交基线缺失: {BASELINE_PATH}"
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["books"]


@pytest.fixture(scope="module")
def quanshu_metrics():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from run_clean_text_metrics import run_book_metrics

    from core.config.settings import Settings

    return run_book_metrics(PDF_DIR / QUANSHU, Settings())


slow_gate = pytest.mark.skipif(
    os.getenv("RUN_CLEAN_TEXT_REGRESSION") != "1"
    or not (PDF_DIR / QUANSHU).exists(),
    reason=(
        "A5 慢回归需 RUN_CLEAN_TEXT_REGRESSION=1 且 DATA/raw_data/law_pdf 含《全书》"
    ),
)


@slow_gate
class TestCleanTextRegression:
    """文本级保真回归：抽取层指标不得劣化提交基线。"""

    def test_header_leak_not_regressed(self, quanshu_metrics):
        base = _load_baseline()["全书"]["header_leak"]
        assert quanshu_metrics["header_leak"] <= base, (
            f"页眉残留劣化 {base} → {quanshu_metrics['header_leak']}"
        )

    def test_page_number_residue_not_regressed(self, quanshu_metrics):
        base = _load_baseline()["全书"]["page_number_res"]
        assert quanshu_metrics["page_number_res"] <= base, (
            f"页码残留劣化 {base} → {quanshu_metrics['page_number_res']}"
        )

    def test_first_line_loss_not_regressed(self, quanshu_metrics):
        base = _load_baseline()["全书"]["first_line_loss"]
        assert quanshu_metrics["first_line_loss"] <= base, (
            f"正文首行丢失劣化 {base} → {quanshu_metrics['first_line_loss']}"
        )

    def test_tier_a_page_scope_stable(self, quanshu_metrics):
        # 路由判定未漂移（A0-2 诊断）：Tier A 页数应与基线一致
        base = _load_baseline()["全书"]["tier_a_pages"]
        assert quanshu_metrics["tier_a_pages"] == base, (
            f"Tier A 页数漂移 {base} → {quanshu_metrics['tier_a_pages']}"
        )
