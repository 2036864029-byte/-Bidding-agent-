# 功能：表格提取引擎注册表（Phase A A3-4）— 按数据形态路由多个表格引擎。
"""
表格提取引擎注册表（A3-4）。

存量核实结论（实证）：当前三本书无矢量线网格表格（``find_tables(lines/default)``
全 0）；``find_tables(text)`` 在双栏/脚注排版上全误判（全书 546/574 页把栏排版
当表格、白如银把脚注缩进当表格）。因此本模块承载「能力与接缝」，**不向 Tier A
管线自动接入**——当前语料无真表，自动检测只会引入误判；等未来招投标业务数据
到达时，按数据形态接入对应引擎并实测。

引擎（注册在 ``ENGINES``）：
  find_tables_engine  数字 PDF（有文本层 + 真网格）→ PyMuPDF ``find_tables``，
                      仅默认/lines 策略（矢量线），不用 text 策略（防双栏误判）。
  html_engine         招投标业务网页数据（招标公告/中标公示等 HTML）→
                      ``pandas.read_html``（独立业务数据入口，非 PDF 路径）。

槽位预留（未实现，数据到达再验证/再装）：
  paddleocr_engine    扫描件无文本层 / 视觉网格 → PaddleOCR PP-Structure。
                      触发条件：出现无文本层扫描件 PDF 或批量视觉网格表格，
                      届时用真实数据 pilot 选型再装。
  column_cluster      文本对齐型表格 → 列聚类（当前语料无真例，未来验证）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# 引擎输出统一为 `|` Markdown 表（每张表一个字符串，多行）。
EngineFn = Callable[[Any], List[str]]


def find_tables_engine(
    page: Any,
    *,
    strategy: str = "lines_strict",
    min_cols: int = 2,
    min_rows: int = 2,
) -> List[str]:
    """PyMuPDF ``find_tables`` 封装：页面（有文本层 + 矢量网格）→ ``|`` 表。

    仅用需要矢量线的策略（``lines_strict`` / ``lines``）——``text`` 策略会把
    双栏排版/脚注缩进误判为表格（存量核实实证）。输出经过列对齐补齐，
    供下游 ``split_table_blocks`` 原子块衔接（Phase B）。
    """
    tables: List[str] = []
    for t in page.find_tables(strategy=strategy).tables:
        rows = t.extract()
        if not rows:
            continue
        ncols = max(len(r) for r in rows)
        if ncols < min_cols or len(rows) < min_rows:
            continue
        lines: List[str] = []
        for r in rows:
            cells = [(c or "").strip().replace("\n", " ") for c in r]
            cells += [""] * (ncols - len(cells))
            lines.append("| " + " | ".join(cells) + " |")
        tables.append("\n".join(lines))
    return tables


def html_engine(html: Any) -> List[str]:
    """``pandas.read_html`` 封装：HTML 表格（招投标业务网页数据）→ ``|`` 表。

    ``html`` 可以是 HTML 字符串、文件路径或 URL。pandas 3.0 的 lxml flavor
    不再自动包装原始 HTML 字符串（会把裸串当文件路径，抛 FileNotFoundError），
    故字符串先经 ``io.StringIO`` 包装成文件对象再解析。
    """
    import io

    import pandas as pd

    if isinstance(html, str):
        html = io.StringIO(html)
    tables: List[str] = []
    try:
        # 显式 lxml flavor：空表/无表时 lxml 直接抛 ValueError（由下方兜底返回 []），
        # 不会转去尝试未安装的 bs4/html5lib 兜底 flavor。
        frames = pd.read_html(html, flavor="lxml")
    except ValueError:
        # 无 <table> 元素或表中无匹配文本（默认 match=".+"）→ 无表可提
        return []
    for df in frames:
        if df.empty:
            continue
        cols = [str(c).strip() for c in df.columns]
        lines = ["| " + " | ".join(cols) + " |"]
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for _, row in df.iterrows():
            cells = [str(c).strip() for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        tables.append("\n".join(lines))
    return tables


# 已实现引擎
ENGINES: Dict[str, EngineFn] = {
    "find_tables": find_tables_engine,
    "html": html_engine,
}

# 槽位预留（未实现）——触发条件见模块 docstring
PADDLEOCR_ENGINE = "paddleocr"
COLUMN_CLUSTER_ENGINE = "column_cluster"


def extract_tables(source: Any, engine: Optional[str] = None) -> List[str]:
    """按数据形态路由到对应引擎，输出 ``|`` Markdown 表列表。

    Args:
        source: PyMuPDF 页面对象（engine="find_tables" 或 PDF 页面自动识别）
                或 HTML 字符串/路径/URL（engine="html"）。
        engine: 显式指定引擎名；None 时按 source 类型自动推断（PDF 页面 →
                find_tables；str/路径/URL → html）。

    Returns:
        ``|`` Markdown 表字符串列表（空表返回 []）。
    """
    if engine is None:
        engine = _infer_engine(source)
    fn = ENGINES.get(engine)
    if fn is None:
        raise KeyError(
            f"未知表格引擎 {engine!r}；已实现: {sorted(ENGINES)}；"
            f"预留: {PADDLEOCR_ENGINE}/{COLUMN_CLUSTER_ENGINE}"
        )
    return fn(source)


def _infer_engine(source: Any) -> str:
    import pymupdf

    if isinstance(source, pymupdf.Page):
        return "find_tables"
    return "html"
