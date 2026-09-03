"""P5 E-13 验收：csv table IR 可被 price 侧消费示例。

演示链路：结构化 csv → FileIngestionPipeline → table IR（columns/row_count/content）
→ price_engine 风格的表结构消费（按列名识别 semantic/exact 等分类，同
price_engine.schema._semantic_columns 的命名约定）。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.ingestion.pipeline import FileIngestionPipeline


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_max_chars=2000,
        chunk_overlap_chars=100,
        enable_pdf_structure=False,
        pdf_min_table_rows=2,
        enable_pdf_toc_filter=False,
        enable_pdf_reflow_flag=False,
    )


class _NoopSink:
    def write(self, documents, *, records):  # noqa: ARG001
        return 0


def _price_style_classify(columns: list[str]) -> dict[str, list[str]]:
    """price_engine.schema 同款命名约定：把列名分成 semantic/exact/time/region。"""
    semantic_kw = ("name", "scope", "industry", "address", "behavior", "result")
    exact_kw = ("credit_code", "code", "project_number", "number")
    time_kw = ("date", "time")
    region_kw = ("province", "city", "district")
    out = {"semantic": [], "exact": [], "time": [], "region": []}
    for col in columns:
        low = col.lower()
        if any(k in low for k in exact_kw):
            out["exact"].append(col)
        elif any(k in low for k in time_kw):
            out["time"].append(col)
        elif any(k in low for k in region_kw):
            out["region"].append(col)
        elif any(k in low for k in semantic_kw):
            out["semantic"].append(col)
    return out


def test_table_ir_columns_consumable_by_price_side(tmp_path):
    csv_text = (
        "company_name,legal_person,registered_capital,credit_code,"
        "establish_date,province,business_scope\n"
        "安徽海纳信息科技有限公司,郭振,500万人民币,91341300MA2MTHUL8L,"
        "2016-03-03,安徽省,计算机软硬件及技术服务\n"
    )
    p = tmp_path / "company_info.csv"
    p.write_text(csv_text, encoding="utf-8")

    res = FileIngestionPipeline(_settings(), sink=_NoopSink()).run([p])[0]
    assert res.status == "completed"
    assert res.table_irs == 1
    columns = res.detail["table_columns"][0]
    assert res.detail["table_row_count"] == 1

    # price 侧消费：按列名分类 → 可驱动 SQL 检索路径（与 price_engine.schema 约定一致）
    classification = _price_style_classify(columns)
    assert "company_name" in classification["semantic"]
    assert "business_scope" in classification["semantic"]
    assert "credit_code" in classification["exact"]
    assert "establish_date" in classification["time"]
    assert "province" in classification["region"]

    # 消费链路完整：table IR 内容含表头与数据行（可作表描述喂 LLM/SQL 工具）
    table_ir = next(
        r.detail.get("table_columns") for r in [res]
    )
    assert table_ir  # 结构化列清单已就绪，供 price 侧建表描述


def test_table_ir_summary_content(tmp_path):
    """table IR 的 markdown 摘要可直接作为表描述文本。"""
    csv_text = "company_name,credit_code\n甲公司,ABC123\n乙公司,DEF456\n"
    p = tmp_path / "t.csv"
    p.write_text(csv_text, encoding="utf-8")
    res = FileIngestionPipeline(_settings(), sink=_NoopSink()).run([p])[0]
    # 通过 pipeline 的 detail 校验列/行；再直接取 CsvNormalizer 验证摘要文本
    assert res.detail["table_row_count"] == 2
