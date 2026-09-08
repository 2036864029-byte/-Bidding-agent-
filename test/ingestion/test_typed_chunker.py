"""TypedChunker 两层切分测试（2026-09-08 修订版语义）。

覆盖：第 1 层全标题切分 + 标题栈元数据、第 2 层超限细分、父子块字段默认值
（一期不落 parent 行）、分流配置表、超大块不产生、内容零丢失。
"""

from __future__ import annotations

from core.config.settings import Settings
from public_kb.ingestion.transforms import (
    DocType,
    TypedChunker,
    chunk_markdown_typed,
    detect_doc_type,
)


def _settings(**overrides: int) -> Settings:
    values = {
        "chunk_max_chars_law": 700,
        "chunk_max_chars_law_compilation": 700,
        "chunk_max_chars_qa": 900,
        "chunk_max_chars_book_practical": 1000,
    }
    values.update(overrides)
    return Settings(**values)


# 真实 cleaned_v1 形态：法名/章平级(#)，条 ##（内联正文）——栈须按语义级而非 md 级
_LAW_MD = """# 中华人民共和国招标投标法

> 发布：2020-12-10　施行：2000-01-01

# 第一章 总 则

## 第一条 为了规范招标投标活动，保护国家利益、社会公共利益和招标投标活动当事人的合法权益，提高经济效益，保证项目质量，制定本法。

## 第二条 在中华人民共和国境内进行招标投标活动，适用本法。
"""


def test_law_splits_at_articles_and_keeps_law_name_in_hierarchy():
    """法规：法名块（沿革正文）+ 每条独立块，层级路径含法名（不被同级的章覆盖）。"""
    docs = TypedChunker(_settings()).chunk(_LAW_MD, "demo_law", DocType.LAW)

    # 法名块：沿革正文独立成块，层级 = 法名本身
    law_block = [d for d in docs if d.metadata["hierarchy_path"] == "中华人民共和国招标投标法"]
    assert law_block, "法名+沿革正文应独立成块"
    assert any("发布" in d.page_content for d in law_block)

    # 条块：每条一个原子块，层级含 法名 / 章 / 条 三级祖先
    articles = [d for d in docs if d.metadata["article_number"]]
    assert len(articles) >= 2
    for d in articles:
        assert d.metadata["chunk_type"] == "atom"
        assert d.metadata["hierarchy_path"].startswith("中华人民共和国招标投标法 / 第一章 总 则 / ")
        assert d.metadata["parent_chunk_id"] == d.metadata["chunk_uid"]
        assert d.metadata["sequence_in_parent"] == 0
    assert any("第一条" in d.metadata["article_number"] for d in articles)


def test_parent_chunk_id_invariant_for_atoms_and_children():
    """父子块不变量：独立块 parent = 自身 uid（分组检索分组键），子块 parent = 合成父标识。"""
    docs = TypedChunker(_settings()).chunk(_LAW_MD, "demo_law", DocType.LAW)
    atoms = [d for d in docs if d.metadata["chunk_type"] == "atom"]
    assert atoms
    for d in atoms:
        assert d.metadata["parent_chunk_id"] == d.metadata["chunk_uid"]

    long_md = (
        "# 中华人民共和国招标投标法\n\n## 第一章 总 则\n\n## 第一条 "
        + "本条内容规定了招标投标活动的基本制度。" * 60
    )
    split = TypedChunker(_settings(chunk_max_chars_law=120)).chunk(
        long_md, "demo_law_split", DocType.LAW
    )
    children = [d for d in split if d.metadata["chunk_type"] == "child"]
    assert children
    for child in children:
        assert child.metadata["parent_chunk_id"].startswith("demo_law_split|sec_")
        assert child.metadata["parent_chunk_id"] != child.metadata["chunk_uid"]


def test_law_article_body_kept_and_no_pure_heading_block():
    """内容零丢失：条块含标题+正文；孤立章标题（无正文）不产块；无纯标题块。"""
    docs = TypedChunker(_settings()).chunk(_LAW_MD, "demo_law", DocType.LAW)
    for d in docs:
        assert d.page_content.strip(), "空块不应产出"
        own = d.metadata["hierarchy_path"].split(" / ")[-1]
        assert d.page_content.strip() != own, f"纯标题块不应产出: {d.page_content!r}"
    first = next(d for d in docs if "第一条" in d.metadata["article_number"])
    assert "第一条" in first.page_content and "制定本法" in first.page_content


def test_content_normalized_no_markdown_markers():
    """内容归一化：chunk 内容不含 `#` 标题标记，标题文本保留（不入 markdown 残渣）。"""
    docs = TypedChunker(_settings()).chunk(_LAW_MD, "demo_law", DocType.LAW)
    for d in docs:
        assert not any(l.lstrip().startswith("#") for l in d.page_content.split("\n"))
    first = next(d for d in docs if "第一条" in d.metadata["article_number"])
    assert first.page_content.startswith("第一条")


def test_heading_stack_metadata_keeps_all_ancestors():
    """标题栈元数据：法名/章/条三级全进 hierarchy_path（含平级 # 的章）。"""
    docs = TypedChunker(_settings()).chunk(_LAW_MD, "demo_law", DocType.LAW)
    art = next(d for d in docs if "第一条" in d.metadata["article_number"])
    assert art.metadata["hierarchy_path"] == (
        "中华人民共和国招标投标法 / 第一章 总 则 / 第一条"
    )


_QA_MD = """## 政府采购概述

###### 1.什么是采购？

答：采购即选择购买，购买通常需要选择。

###### 2.什么是政府采购？

答：政府采购是指利用财政资金进行采购的行为。
"""


def test_qa_splits_at_question_and_sets_question_field():
    """问答：一问一答为一个原子块，question 字段提取，内容=问题行+答案。"""
    docs = TypedChunker(_settings()).chunk(_QA_MD, "demo_qa", DocType.QA)
    atoms = [d for d in docs if d.metadata["chunk_type"] == "atom"]
    assert len(atoms) == 2
    assert atoms[0].metadata["question"] == "什么是采购？"
    assert atoms[1].metadata["question"] == "什么是政府采购？"
    assert "政府采购概述" in atoms[0].metadata["hierarchy_path"]
    assert atoms[0].page_content.startswith("1.什么是采购？")
    assert "答：采购即选择购买" in atoms[0].page_content


def test_qa_long_answer_children_with_question_prefix():
    """问答超长：不落 parent 行，child 前缀带完整问题，指向合成父标识。"""
    long_answer = "答：" + "这是答案正文。这是一句比较长的话。这是一句比较长的话。这是一句比较长的话。" * 20
    md = (
        "## 采购方式\n\n"
        f"###### 1.招标与采购的关系？\n\n{long_answer}\n"
    )
    docs = TypedChunker(_settings(chunk_max_chars_qa=80)).chunk(md, "demo_qa", DocType.QA)
    assert all(d.metadata["chunk_type"] in ("atom", "child") for d in docs)
    children = [d for d in docs if d.metadata["chunk_type"] == "child"]
    assert children, "超长问答应切出 child"
    for child in children:
        assert child.metadata["parent_chunk_id"].startswith("demo_qa|sec_")
        assert child.metadata["sequence_in_parent"] >= 1
        assert child.page_content.startswith("问：招标与采购的关系？")
        assert len(child.page_content) <= 80


_BOOK_MD = """## 第一章 招标投标基础知识

### 第一节 招标投标概述

#### 一、招标投标的起源

招标投标起源于英国。这是第一段足够长用来验证分块边界的内容，它不会跨到下一小节。

#### 二、招标投标的概念

招标投标是一种竞争性交易方式。这里是第二小节的内容。

### 第二节 招标投标的原则

这里是第二节的内容，不应混入第一节的子块。
"""


def test_book_splits_at_sections_and_does_not_cross_boundary():
    """实务书：按节切分，超长小节子块不跨入下一节。"""
    docs = TypedChunker(_settings()).chunk(_BOOK_MD, "demo_book", DocType.BOOK_PRACTICAL)
    b2 = [d for d in docs if "第二节" in d.metadata["hierarchy_path"]]
    assert b2
    assert all("招标投标的原则" in d.page_content for d in b2)
    assert all("招标投标概述" not in d.page_content for d in b2)


def test_oversize_law_splits_at_items_and_stays_under_threshold():
    """超限法规：按款项 `- （一）` 切 child，每块 ≤ 阈值，共享层级，无 parent 行。"""
    md = """# 中华人民共和国招标投标法

## 第一章 总 则

## 第一条 本条规定了招标投标活动应当遵循的各项基本制度与实施规则。

- （一）第一项内容，涉及招标投标活动的组织、实施与监督管理，应当遵循公开、公平、公正和诚实信用的原则。
- （二）第二项内容，涉及招标投标活动的程序规范，包括招标公告的发布、投标文件的编制与递交等环节。
- （三）第三项内容，涉及招标投标活动的法律责任与救济途径，以及相关争议的处理机制。
"""
    docs = TypedChunker(_settings(chunk_max_chars_law=120)).chunk(
        md, "demo_law_items", DocType.LAW
    )
    assert all(d.metadata["chunk_type"] in ("atom", "child") for d in docs)
    children = [d for d in docs if d.metadata["chunk_type"] == "child"]
    assert len(children) >= 3
    for child in children:
        assert len(child.page_content) <= 120
        assert child.metadata["parent_chunk_id"].startswith("demo_law_items|sec_")
        assert child.metadata["sequence_in_parent"] >= 1
        assert child.metadata["article_number"] == "第一条"
        assert "招标投标法 / 第一章 总 则 / 第一条" in child.metadata["hierarchy_path"]
    # 不跨边界：每个 child 只含一个款项目
    for child in children:
        assert child.page_content.count("（") <= 1


def test_compilation_no_superblock():
    """法规全书超大块回归：55 部法各自成块起点，任何 chunk ≤ 阈值。"""
    md = []
    for i in range(55):
        md.append(f"# 中华人民共和国第{i}部法")
        md.append("## 第一章 总则")
        md.append(f"#### 第{i + 1}条 这是第{i}部法的第一条正文内容，为了规范相关活动而制定。")
    docs = TypedChunker(_settings()).chunk(
        "\n\n".join(md), "模拟法规全书", DocType.LAW_COMPILATION
    )
    assert len(docs) == 55  # 每部法 1 条
    assert all(len(d.page_content) <= 700 for d in docs)
    assert all(len(d.page_content) <= 65535 for d in docs)
    roots = {d.metadata["hierarchy_path"].split(" / ")[0] for d in docs}
    assert len(roots) == 55  # 55 个 # 法名 全部成为独立块起点


def test_detect_doc_type_by_path():
    """分流配置表：目录规则 → 文件名关键词 → 目录兜底 → 未知默认 book_practical。"""
    assert detect_doc_type("DATA/raw_data/cleaned_v1/markdown/a.md") == DocType.LAW
    assert detect_doc_type("DATA/raw_data/handoff_v3/clean/1200问.md") == DocType.QA
    assert detect_doc_type("DATA/raw_data/handoff_v3/clean/实务解读.md") == DocType.BOOK_PRACTICAL
    assert detect_doc_type(
        "DATA/raw_data/handoff_v3/clean/招标投标法律法规全书.md"
    ) == DocType.LAW_COMPILATION
    # 未命中任何规则（未知目录）→ 默认 book_practical 风格
    assert detect_doc_type("some/unknown/dir/anything.md") == DocType.BOOK_PRACTICAL


def test_chunk_markdown_typed_auto_detect():
    """便捷函数：路径自动识别 doc_type；无路径默认 book_practical。"""
    docs = chunk_markdown_typed(
        _BOOK_MD, "demo_book", path="DATA/raw_data/handoff_v3/clean/实务指南.md"
    )
    assert docs and all(d.metadata["doc_type"] == "book_practical" for d in docs)
    docs = chunk_markdown_typed(
        _LAW_MD, "demo_law", path="DATA/raw_data/cleaned_v1/markdown/a.md"
    )
    assert docs and all(d.metadata["doc_type"] == "law" for d in docs)
