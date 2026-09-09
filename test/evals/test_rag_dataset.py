"""evals.datasets.rag — 金标集契约与抽样离线测试（E1-1 / E1-2）。"""

from __future__ import annotations

import json

from evals.datasets import (
    EvalSample,
    distribution,
    dump_jsonl,
    load_jsonl,
    split_holdout,
    stratified_sample,
)


def _sample(q: str, qt: str = "single_hop", n_chunks: int = 1) -> EvalSample:
    return EvalSample(
        question=q,
        reference=f"ref-{q}",
        ground_truth_chunk_ids=list(range(n_chunks)),
        ground_truth_text=["text"],
        query_type=qt,
    )


def _corpus() -> list[EvalSample]:
    # 65 single + 35 multi（与真实金标比例一致）
    return [_sample(f"s{i}") for i in range(65)] + [
        _sample(f"m{i}", qt="multi_hop", n_chunks=2) for i in range(35)
    ]


def test_load_dump_roundtrip(tmp_path):
    corpus = _corpus()
    path = tmp_path / "x.jsonl"
    dump_jsonl(corpus, path)

    loaded = load_jsonl(path)

    assert len(loaded) == 100
    assert loaded[0].question == "s0"
    assert loaded[-1].ground_truth_chunk_ids == [0, 1]


def test_load_reports_bad_line_with_lineno(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"question": "ok"}\nnot-json\n', encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match=":2"):
        load_jsonl(path)


def test_stratified_sample_preserves_proportion():
    picked = stratified_sample(_corpus(), 100)

    dist = distribution(picked)
    assert len(picked) == 100
    assert dist == {"single_hop": 65, "multi_hop": 35}


def test_stratified_sample_smaller_size_keeps_ratio():
    picked = stratified_sample(_corpus(), 20)

    dist = distribution(picked)
    assert len(picked) == 20
    # 20 × 65% = 13，20 × 35% = 7
    assert dist == {"single_hop": 13, "multi_hop": 7}


def test_stratified_sample_reproducible_with_same_seed():
    a = stratified_sample(_corpus(), 20, seed=7)
    b = stratified_sample(_corpus(), 20, seed=7)

    assert [s.question for s in a] == [s.question for s in b]


def test_stratified_sample_changes_with_seed():
    a = stratified_sample(_corpus(), 20, seed=1)
    b = stratified_sample(_corpus(), 20, seed=2)

    assert [s.question for s in a] != [s.question for s in b]


def test_stratified_sample_oversize_returns_all():
    corpus = _corpus()
    picked = stratified_sample(corpus, 999)

    assert len(picked) == len(corpus)


def test_split_holdout_disjoint_and_sized():
    corpus = _corpus()
    dev, holdout = split_holdout(corpus, 20, seed=42)

    assert len(holdout) == 20
    assert len(dev) == 80
    assert {s.question for s in dev}.isdisjoint({s.question for s in holdout})
    assert distribution(holdout) == {"single_hop": 13, "multi_hop": 7}


def test_sample_is_json_serializable():
    payload = _sample("q").model_dump()

    json.dumps(payload, ensure_ascii=False)
    assert payload["query_type"] == "single_hop"
