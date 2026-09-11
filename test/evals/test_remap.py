"""evals.datasets.remap — 金标 chunk_id 重映射离线测试（E1-5）。"""

from __future__ import annotations

from evals.datasets import EvalSample
from evals.datasets.remap import build_index, match_passage, remap_samples

CHUNKS = [
    {"id": 101, "chunk_uid": "ck-a", "text": "第十三条 申请单位应在一个月内办理。"},
    {"id": 102, "chunk_uid": "ck-b", "text": "第十六条 会员合并后新设立的法人应提出申请。"},
]


def test_build_index_exact():
    idx = build_index(CHUNKS)
    assert len(idx["exact"]) == 2 and len(idx["all"]) == 2


def test_match_exact_normalized_and_none():
    idx = build_index(CHUNKS)
    assert match_passage("第十三条 申请单位应在一个月内办理。", idx) == (101, "ck-a", "exact")
    # 空白差异（排版）仍应精确命中
    assert match_passage("第十三条\n\n申请单位应在一个月内办理。", idx)[0] == 101
    assert match_passage("完全不存在的内容", idx) == (None, None, "none")
    assert match_passage("", idx) == (None, None, "none")


def test_match_contained_fallback():
    idx = build_index([{"id": 1, "chunk_uid": "u",
                        "text": "第十三条 申请单位应在一个月内办理如下事项，逾期视为放弃资格。"}])
    cid, uid, how = match_passage("申请单位应在一个月内办理如下事项", idx)
    assert cid == 1 and uid == "u" and how == "contained"


def test_remap_samples_backfills_ids_and_uids():
    s = EvalSample(question="q", ground_truth_text=["第十三条 申请单位应在一个月内办理。"],
                   ground_truth_chunk_ids=[999], query_type="single_hop")
    new, stats = remap_samples([s], build_index(CHUNKS))
    assert new[0].ground_truth_chunk_ids == [101]
    assert new[0].ground_truth_chunk_uids == ["ck-a"]
    assert stats["exact"] == 1 and stats["none"] == 0
    assert stats["samples_all_matched"] == 1


def test_remap_partial_match_flags_not_all():
    s = EvalSample(question="q",
                   ground_truth_text=["第十三条 申请单位应在一个月内办理。", "不存在段落"],
                   query_type="multi_hop")
    new, stats = remap_samples([s], build_index(CHUNKS))
    assert new[0].ground_truth_chunk_ids == [101]   # 只回填命中的
    assert stats["exact"] == 1 and stats["none"] == 1
    assert stats["samples_all_matched"] == 0
