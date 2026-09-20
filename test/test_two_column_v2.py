# 功能：P2 阶段测试——双栏路径修复（通栏行 y 归位 + 串栏防御 + 双栏判定）。
"""test_two_column_v2 —— 版面还原计划 §6.1 P2 阶段测试（先红后绿）。

被测契约（计划 P2）：
  - `pdf_two_column_reflow.reflow_two_columns_v2(lines, split_x, page_width)`
      通栏行（行宽/页宽 ≥ 0.7）**按 y 归位**插入阅读流：通栏行把页面切成
      上下两半，正确序 = 左(上)→右(上)→通栏行→左(下)→右(下)；
      不得再整体前置（旧 `full+left+right` 的页中章标题抽离缺陷）。
  - `pdf_two_column_reflow.needs_two_column_defense(lines, page_width) -> bool`
      `text` 路径串栏防御信号：行宽中位数 < 0.55*页宽 且 x0 分布呈双峰
      → True（应转双栏处理）；真单栏 → False。
  - `pdf_page_profile.detect_two_columns(blocks, page_width)`（公开封装）
      x0 间隙直判。历史注记：曾加 x1 双峰兜底，2026-09-06 三书实测证明其
      无判别力（两端对齐单栏页 x1 假双峰 → 书2 近 50% 页洪水送 MinerU）
      已回退；x0 无间隙的表单页由解析时防御兜住。

泛化护栏：阈值全部为页宽/行宽相对比例；合成页不含书名/页码硬编码。
"""

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import TextLine
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import (  # noqa: F401
    detect_two_columns,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (  # noqa: F401
    needs_two_column_defense,
    reflow_two_columns_v2,
)

PAGE_W = 595.0
SPLIT_X = PAGE_W / 2.0
COL_W = PAGE_W / 2.0 - 40.0  # 每栏可用宽度


def col_line(text, y, *, col="left", width=None):
    x0 = 30.0 if col == "left" else SPLIT_X + 10.0
    w = width if width is not None else min(len(text) * 10.0, COL_W)
    return TextLine(text=text, x0=x0, y0=y, x1=x0 + w, y1=y + 10.5,
                    size=10.5, bold=False, centered=False)


def full_line(text, y):
    w = PAGE_W * 0.8  # 跨栏通栏
    return TextLine(text=text, x0=30.0, y0=y, x1=30.0 + w, y1=y + 14.0,
                    size=14.0, bold=True, centered=True)


# ── P2-1 通栏行按 y 归位 ─────────────────────────────────

def test_midpage_full_width_heading_not_hoisted():
    """页中通栏章标题不得被提到页首：左上→右上→标题→左下→右下。"""
    lines = [
        col_line("第四章开标评标定标活动的程序规定陆续出台，各地", 60.0, col="left"),
        col_line("第四章开标评标活动在招标文件确定的地点公开进行，", 60.0, col="right"),
        full_line("第五章 法律责任", 300.0),
        col_line("招标人应当自中标通知书发出之日起三十日内，按照", 400.0, col="left"),
        col_line("投标文件中载明的事项与中标人订立书面合同的义务。", 400.0, col="right"),
    ]
    out = reflow_two_columns_v2(lines, split_x=SPLIT_X, page_width=PAGE_W)
    texts = [ln.text for ln in out]
    i = texts.index("第五章 法律责任")
    # 标题之前：只有上半页内容；标题之后：下半页内容
    assert texts[:i] == [
        "第四章开标评标定标活动的程序规定陆续出台，各地",
        "第四章开标评标活动在招标文件确定的地点公开进行，",
    ]
    assert texts[i + 1:] == [
        "招标人应当自中标通知书发出之日起三十日内，按照",
        "投标文件中载明的事项与中标人订立书面合同的义务。",
    ]


def test_top_full_width_heading_still_first():
    """页顶通栏标题行为不变（先于两栏全部内容）。"""
    lines = [
        full_line("第二章 招标投标基础知识", 50.0),
        col_line("招标投标作为一种极具竞争性的交易方式起源较早。", 100.0, col="left"),
        col_line("招标投标作为一种极具竞争性的交易方式应用广泛。", 100.0, col="right"),
    ]
    out = reflow_two_columns_v2(lines, split_x=SPLIT_X, page_width=PAGE_W)
    assert out[0].text == "第二章 招标投标基础知识"
    assert [ln.text for ln in out[1:]] == [
        "招标投标作为一种极具竞争性的交易方式起源较早。",
        "招标投标作为一种极具竞争性的交易方式应用广泛。",
    ]


# ── P2-2 text 路径串栏防御 ───────────────────────────────

def test_defense_true_for_interleaved_two_col_page():
    """误判为单栏的双栏页：窄行交替 + x0 双峰 → 防御信号触发。"""
    lines = []
    y = 80.0
    for k in range(10):  # 左右栏逐行交错（串栏页特征）
        lines.append(col_line("左栏内容行文本若干字左右宽度的一半左右。", y, col="left"))
        lines.append(col_line("右栏内容行文本若干字左右宽度的一半左右。", y + 5.0, col="right"))
        y += 18.0
    assert needs_two_column_defense(lines, page_width=PAGE_W) is True


def test_defense_false_for_true_single_column():
    lines = [
        TextLine(text="单栏正文整行铺满页面宽度直到行尾才换行的一行长文本内容。",
                 x0=72.0, y0=100.0 + k * 16.0, x1=523.0, y1=116.0 + k * 16.0,
                 size=10.5, bold=False, centered=False)
        for k in range(10)
    ]
    assert needs_two_column_defense(lines, page_width=PAGE_W) is False


# ── P2-3 双栏检测回归（2026-09-06 x1 兜底回退）────────
# x1（右边缘）双峰兜底实测无判别力：两端对齐单栏页"齐右大簇 vs 段末短行"
# 天然形成 x1 假双峰，书2 近 50% 单栏页被误判 → 洪水送 MinerU。分档层
# 回归 x0 间隙直判；x0 无间隙的表单页由解析时防御兜住。

def test_justified_single_col_not_flagged_two_col():
    """两端对齐单栏页：齐右大簇 + 段末短行形成 x1 假双峰 → 不得判双栏。"""
    blocks = []
    for k in range(10):
        y = 100.0 + k * 30.0
        blocks.append((72.0, y, 523.0, y + 14.0))       # 通栏齐右行
    blocks.append((72.0, 400.0, 380.0, 414.0))           # 段末短行
    blocks.append((72.0, 430.0, 300.0, 444.0))           # 段末短行
    blocks.append((85.0, 460.0, 410.0, 474.0))           # 段末短行
    gap, split_x, has_two_col = detect_two_columns(blocks, page_width=PAGE_W)
    assert has_two_col is False


def test_single_col_not_detected_by_x1():
    blocks = [
        (72.0, 100.0 + k * 30.0, 523.0, 114.0 + k * 30.0)  # 通栏行
        for k in range(10)
    ]
    gap, split_x, has_two_col = detect_two_columns(blocks, page_width=PAGE_W)
    assert has_two_col is False


def test_no_page_or_book_hardcode():
    import inspect

    import public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow as mod
    src = inspect.getsource(mod)
    for banned in ("白如银", "1200问", "法律法规全书", "== 145", "==145"):
        assert banned not in src, f"实现中出现疑似硬编码: {banned}"
