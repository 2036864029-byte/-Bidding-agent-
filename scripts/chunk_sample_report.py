# 功能：分块小样本验证报告（阶段 2 强制门槛）——落盘 documents.jsonl + 打印核对项。
"""文本分块优化阶段 2：小样本验证报告。

每类型抽代表性样本分块落盘，核对修订版验收项：
  块数 / 最大块长(≤阈值、无>65535) / 条号·问项是否全部成块 / 正文完整(无纯标题块)
  / hierarchy_path 是否含祖先层级 / 父子块字段默认值(一期不落 parent 行)。

用法：
    python scripts/chunk_sample_report.py [--out-dir DATA/work/chunks_sample]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根，供 core/ 等导入

from core.config.settings import Settings
from public_kb.ingestion.transforms import DocType, TypedChunker
from public_kb.ingestion.handoff import dump_documents_jsonl

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

LAW_DIR = Path("DATA/raw_data/cleaned_v1/markdown")
HANDOFF = Path("DATA/raw_data/handoff_v3/clean")

LAW_SAMPLES = [
    "006acd58e28d589a_上海市政府采购实施办法.md",                      # 短条
    "bccbc0404b4fdabe_公路水运工程监理企业资质管理规定.md",            # 长条
    "c7618d3f095155e4_交通运输行政执法程序规定.md",                    # 含款（207 处）
    "5bc2e72c17eff409_政府采购货物和服务招标投标管理办法.md",          # 含款（120 处）
    "8c24a00443e43c24_保险公司股权管理办法(2018).md",                 # 含款（188 处）
]
QA_FILE = "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.assembled.md"
BOOK_FILE = "招标投标法律解读与风险防范实务 (白如银) (Z-Library).assembled.md"
COMP_FILE = "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"


@dataclass
class SampleReport:
    label: str
    doc_type: DocType
    total: int = 0
    atoms: int = 0
    children: int = 0
    max_len: int = 0
    over_threshold: int = 0
    over_65535: int = 0
    pure_heading: int = 0
    bad_parent_field: int = 0
    multi_level_hierarchy: int = 0
    article_numbers: set = field(default_factory=set)
    questions: int = 0
    roots: set = field(default_factory=set)
    notes: List[str] = field(default_factory=list)


def chunk_file(path: Path, doc_type: DocType, doc_id: str) -> list:
    settings = Settings()
    chunker = TypedChunker(settings)
    return chunker.chunk(
        path.read_text(encoding="utf-8"),
        path.stem,
        doc_type,
        doc_id=doc_id,
        source_format="text",
    )


def summarize(report: SampleReport, docs: list) -> None:
    from langchain_core.documents import Document

    for d in docs:
        m = d.metadata
        report.total += 1
        ln = len(d.page_content)
        report.max_len = max(report.max_len, ln)
        if m["chunk_type"] == "atom":
            report.atoms += 1
        elif m["chunk_type"] == "child":
            report.children += 1
        else:
            report.notes.append(f"未知 chunk_type={m['chunk_type']}")
        # 阈值按类型
        cfg = {DocType.LAW: 700, DocType.LAW_COMPILATION: 700,
               DocType.QA: 900, DocType.BOOK_PRACTICAL: 1000}[report.doc_type]
        if ln > cfg:
            report.over_threshold += 1
        if ln > 65535:
            report.over_65535 += 1
        if any(l.strip() and not l.lstrip().startswith("#")
               for l in d.page_content.split("\n")) is False:
            # 归一化后内容无 # 标记：改为「内容仅剩自己的标题」才算纯标题块
            own = m["hierarchy_path"].split(" / ")[-1] if m["hierarchy_path"] else ""
            if d.page_content.strip() == own:
                report.pure_heading += 1
        # 父子字段默认值
        if m["chunk_type"] == "atom" and (m["parent_chunk_id"] != "" or m["sequence_in_parent"] != 0):
            report.bad_parent_field += 1
        if m["chunk_type"] == "child" and not m["parent_chunk_id"].startswith(f"{m['doc_id']}|sec_"):
            report.bad_parent_field += 1
        if m["chunk_type"] == "child" and m["sequence_in_parent"] < 1:
            report.bad_parent_field += 1
        # 层级祖先
        if m["hierarchy_path"].count(" / ") >= 1:
            report.multi_level_hierarchy += 1
        if m["article_number"]:
            report.article_numbers.add(m["article_number"])
        if m["question"]:
            report.questions += 1
        if m["hierarchy_path"] and " / " not in m["hierarchy_path"]:
            report.roots.add(m["hierarchy_path"])


def print_report(r: SampleReport) -> None:
    cfg = {DocType.LAW: 700, DocType.LAW_COMPILATION: 700,
           DocType.QA: 900, DocType.BOOK_PRACTICAL: 1000}[r.doc_type]
    print(f"\n■ {r.label}  [{r.doc_type.value}]  (阈值 {cfg})")
    print(f"  块数={r.total}  atom={r.atoms}  child={r.children}")
    print(f"  最大块长={r.max_len}  (≤{cfg}? {r.max_len <= cfg})  >65535={r.over_65535}")
    print(f"  超阈值块={r.over_threshold}  纯标题块={r.pure_heading}  父子字段异常={r.bad_parent_field}")
    print(f"  含祖先层级块={r.multi_level_hierarchy}/{r.total}")
    if r.article_numbers:
        print(f"  去重条号数={len(r.article_numbers)}")
    if r.questions:
        print(f"  问项块数={r.questions}")
    if r.roots:
        print(f"  顶级根数={len(r.roots)}: {sorted(r.roots)[:6]}{'…' if len(r.roots) > 6 else ''}")
    for n in r.notes[:5]:
        print(f"  ! {n}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="DATA/work/chunks_sample")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    reports: List[SampleReport] = []

    # ── law：5 部法规 ──
    for i, name in enumerate(LAW_SAMPLES, start=1):
        p = LAW_DIR / name
        doc_id = f"law_s{i:02d}"
        docs = chunk_file(p, DocType.LAW, doc_id)
        dump_documents_jsonl(docs, out / f"{doc_id}.documents.jsonl")
        r = SampleReport(f"law {i}: {name[:30]}", DocType.LAW)
        summarize(r, docs)
        reports.append(r)

    # ── qa：前 50 问项 ──
    qa = chunk_file(HANDOFF / QA_FILE, DocType.QA, "qa_s01")
    dump_documents_jsonl(qa, out / "qa_s01.documents.jsonl")
    r = SampleReport("qa: 1200问（前 50 问项）", DocType.QA)
    summarize(r, qa)
    q_chunks = [d for d in qa if d.metadata["question"]]
    r.notes.append(f"总问项块={len(q_chunks)}，前 50 问项抽查中……")
    for d in q_chunks[:50]:
        m = d.metadata
        ok_len = len(d.page_content) <= 900
        ok_body = any(l.strip() and not l.lstrip().startswith("#")
                      for l in d.page_content.split("\n"))
        if not ok_len or not ok_body:
            r.notes.append(f"  ✗ {m['question'][:20]}: len={len(d.page_content)} body={ok_body}")
    r.notes.append(f"前 50 问项全部块长≤900 且含正文 = "
                   f"{not any(n.startswith('  ✗') for n in r.notes)}")
    reports.append(r)

    # ── book：前 1 章 ──
    book = chunk_file(HANDOFF / BOOK_FILE, DocType.BOOK_PRACTICAL, "book_s01")
    dump_documents_jsonl(book, out / "book_s01.documents.jsonl")
    r = SampleReport("book: 白如银实务书（前 1 章）", DocType.BOOK_PRACTICAL)
    summarize(r, book)
    first_chapter = next(
        (d.metadata["hierarchy_path"].split(" / ")[0] for d in book
         if d.metadata["hierarchy_path"]),
        "?",
    )
    chap_chunks = [d for d in book if first_chapter in d.metadata["hierarchy_path"]]
    r.notes.append(f"首章「{first_chapter[:20]}」块数={len(chap_chunks)}"
                   f"（全书块数={len(book)}）")
    for d in chap_chunks[:5]:
        r.notes.append(f"    层级: {d.metadata['hierarchy_path']}")
    reports.append(r)

    # ── law_compilation：前 2 部法 + 1 篇无条号政策 ──
    comp = chunk_file(HANDOFF / COMP_FILE, DocType.LAW_COMPILATION, "comp_s01")
    dump_documents_jsonl(comp, out / "comp_s01.documents.jsonl")
    r = SampleReport("compilation: 法规全书（前 2 部法 + 1 篇无条号政策）", DocType.LAW_COMPILATION)
    summarize(r, comp)
    # 按根（法名）聚合
    from collections import OrderedDict
    by_root: dict = OrderedDict()
    for d in comp:
        root = d.metadata["hierarchy_path"].split(" / ")[0]
        by_root.setdefault(root, []).append(d)
    r.notes.append(f"法名根数={len(by_root)}，全书块数={len(comp)}")
    for idx, (root, chunks) in enumerate(list(by_root.items())[:2], start=1):
        max_art = max(len(c.metadata["article_number"]) > 0 for c in chunks)
        r.notes.append(f"  法{idx}「{root[:24]}」块数={len(chunks)} 含条号={max_art}")
    # 找无条号政策根（块数少且无条号）
    no_art_roots = [root for root, chs in by_root.items()
                    if chs and not any(c.metadata["article_number"] for c in chs)]
    if no_art_roots:
        root = no_art_roots[0]
        r.notes.append(f"  无条号政策「{root[:24]}」块数={len(by_root[root])}")
    reports.append(r)

    print("\n" + "=" * 64)
    print("小样本分块验证报告（阶段 2，交用户确认门槛）")
    print("=" * 64)
    for r in reports:
        print_report(r)
    print(f"\njsonl 已落盘: {out.resolve()}")

    # 汇总：任何超阈值/纯标题/字段异常都记为不通过
    fails = [r for r in reports if r.over_threshold or r.over_65535
             or r.pure_heading or r.bad_parent_field]
    print(f"\n结论: {'✅ 全部通过（无超阈值/纯标题/字段异常）' if not fails else '❌ 存在异常'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
