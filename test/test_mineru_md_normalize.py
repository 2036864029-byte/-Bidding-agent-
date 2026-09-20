# 功能：P6 阶段测试——MinerU 外来 markdown 标题归一（版面还原计划，2026-09-06）。
"""test_mineru_md_normalize —— MinerU 外来 md 标题统一到本地语义。

背景：MinerU 对标题一律打扁平 ``#``（章节/编号问项/题注零层级）；本地页标题是
版面锚定级、clean v2 又跳过 A3-1 → 外来 # 原样混入，同一语义两级并存。
``normalize_mineru_md_heading_levels`` 在 MinerU md 并入前按文本模式重定级。

被测契约：
  - ``第X编/章/节/条`` 结构词 → 1/2/3/4；
  - 列表/编号 ``一、/（一）/N.`` → 4/5/6（N. 覆盖书3 手册体编号问项）；
  - 图/表/附表/案例/例题/封面题注 → 剥 ``#`` 降正文；
  - 无模式内容章（"政府采购概述"）→ 默认 2；
  - 非标题行、内部表格原样保留；幂等（已是目标级不再动）。
泛化护栏：全部模式驱动、阈值相对，无书名/页码硬编码。
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from public_kb.ingestion.normalizers.pdf.pdf_markdown_assembler import (  # noqa: E402
    normalize_mineru_md_heading_levels,
)


def test_structural_words_get_fixed_levels():
    md = "# 第二章\n# 第一节\n# 第十八节\n正文行。\n"
    out = normalize_mineru_md_heading_levels(md)
    assert "## 第二章" in out
    assert "### 第一节" in out
    assert "### 第十八节" in out


def test_numbered_questions_become_leaf():
    md = "# 65.政府采购有哪些特点？\n# 110.邀请谈判方式采购的范围是什么？\n答：正文。\n"
    out = normalize_mineru_md_heading_levels(md)
    assert "###### 65.政府采购有哪些特点？" in out
    assert "###### 110.邀请谈判方式采购的范围是什么？" in out


def test_list_sublevels():
    md = "# 一、采购方式\n# （一）公开原则\n正文。\n"
    out = normalize_mineru_md_heading_levels(md)
    assert "#### 一、采购方式" in out
    assert "##### （一）公开原则" in out


def test_captions_demoted_to_body():
    md = ("# 图1-1 公开招标和邀请招标的流程图\n"
          "# 表5-1 综合评分法投标评分记录\n"
          "# 案例5-61\n# 例题5-5 （运费折价）\n正文。\n")
    out = normalize_mineru_md_heading_levels(md)
    for cap in ("图1-1 公开招标和邀请招标的流程图",
                "表5-1 综合评分法投标评分记录",
                "案例5-61", "例题5-5 （运费折价）"):
        assert f"# {cap}" not in out
        assert out.split("\n")[0] == "图1-1 公开招标和邀请招标的流程图"  # 剥 # 成正文行


def test_unpatterned_content_title_defaults_to_chapter():
    md = "# 政府采购概述\n# 施工投标及策略\n正文。\n"
    out = normalize_mineru_md_heading_levels(md)
    assert "## 政府采购概述" in out
    assert "## 施工投标及策略" in out


def test_body_and_tables_untouched():
    md = "# 65.政府采购有哪些特点？\n| 列1 | 列2 |\n| 甲 | 乙 |\n普通正文句。\n"
    out = normalize_mineru_md_heading_levels(md)
    assert "| 列1 | 列2 |" in out
    assert "普通正文句。" in out


def test_idempotent():
    md = "# 65.政府采购有哪些特点？\n# 第二章\n# 图1-1 x\n正文。\n"
    once = normalize_mineru_md_heading_levels(md)
    assert normalize_mineru_md_heading_levels(once) == once


def test_no_book_or_page_hardcode():
    import inspect

    import public_kb.ingestion.normalizers.pdf.pdf_markdown_assembler as mod
    src = inspect.getsource(mod)
    for banned in ("白如银", "1200问", "法律法规全书", "== 145"):
        assert banned not in src, f"实现中出现疑似硬编码: {banned}"
