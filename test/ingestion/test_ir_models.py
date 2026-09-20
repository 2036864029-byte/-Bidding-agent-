"""IR 模型 + 文档级哈希/SimHash 离线测试（方案 Phase 5 / E-1）。"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)


from public_kb.ingestion.ir import (
    NormalizedDocument,
    compute_source_hash,
    hamming_distance,
    simhash,
)


def test_normalized_document_constructs():
    doc = NormalizedDocument(
        doc_id="a/b.pdf", source_format="pdf", ir_format="markdown",
        content="正文", metadata={"quality_score": 0.9},
    )
    assert doc.doc_id == "a/b.pdf"
    assert doc.metadata["quality_score"] == 0.9


def test_source_hash_is_md5_of_bytes(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello")
    assert compute_source_hash(p) == "5d41402abc4b2a76b9719d911017c592"
    # 内容变化 → 哈希变化
    p.write_bytes(b"hello!")
    assert compute_source_hash(p) != "5d41402abc4b2a76b9719d911017c592"


def test_simhash_identical_and_near_texts():
    a = simhash("第一章 总则\n第一条 为了规范招标投标活动…" * 5)
    b = simhash("第一章 总则\n第一条 为了规范招标投标活动…" * 5)
    c = simhash("第二章 完全不同\n另一篇法律文书内容，与上者毫无关联。" * 5)
    assert a == b
    # 相同文本海明距离 0，完全不同文本距离应明显更大
    assert hamming_distance(a, b) == 0
    assert hamming_distance(a, c) > 8


def test_simhash_tolerant_to_small_edits():
    base = "政府采购法 第五十条 采购人应当在采购文件确定的提交投标文件截止时间至少十五日前发布招标公告" * 3
    edited = base.replace("十五日前", "十日前")  # 小改动
    random_text = "完全无关的另外一篇文档内容，用于对照" * 6
    small = hamming_distance(simhash(base), simhash(edited))
    large = hamming_distance(simhash(base), simhash(random_text))
    # 相对性质：小改动的距离显著小于无关文本（不做绝对阈值——小尺度下波动大）
    assert small < large
    assert large >= 8


def test_simhash_empty():
    assert simhash("") == 0
    assert simhash("   \n  ") == 0


def test_hamming_distance_basic():
    assert hamming_distance(0b0000, 0b0000) == 0
    assert hamming_distance(0b0000, 0b1111) == 4
    assert hamming_distance(0b1010, 0b1100) == 2
