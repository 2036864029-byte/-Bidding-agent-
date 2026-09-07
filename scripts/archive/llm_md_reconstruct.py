# 功能：Markdown 文档结构重构引擎（LLM 语义重构）——rule.md + rule1.md 执行器。
"""
ETL 文档清洗 + Markdown 结构重构（rule.md 2026-09-05 + rule1.md 重构要求）。

架构护栏（严格遵守）：
  - 100% 隔离，**绝不修改** relayout.py / handoff.py / cleaner.py 等主流程。
  - 本地 Python 仅做切片搬运与落盘：LLM 返回文本**零加工**原样写入目标文件，
    **禁止任何正则替换 / 二次折行 / 排版加工**。
  - System Prompt 原封取自 rule.md 第三节约定指令。
  - 模型默认 deepseek-v4-flash（--model 覆盖）。

rule1.md 重构要点（已落实）：
  1. 忠实度校验废除 `difflib.SequenceMatcher`，改用 `collections.Counter`
     字符频次比对（毫秒级，<0.001s 量级）——消除本地 O(N^2) CPU 卡死。
  2. `ThreadPoolExecutor(max_workers=5)` 并发调用 API（护栏：并发 ≤5，防 429）。
  3. 每个任务绑定 page_id/chunk_id；结果先存内存字典，**全部完成后按 id 升序
     组装、一次性落盘**（防页码错乱）。
  4. 控制台逐条打印：`[Page X] API响应耗时: Y.YYs | 本地校验耗时: Z.ZZs | 忠实度: W.WW`。

用法：
  ping                    验证模型/通道（单次小请求）
  pilot --n 50 ...        三书按页占比随机均匀抽 n 页 → 并发重构 → 验收报表
  full --chunk 3500 ...   逐书按自然段界切块 → 并发重构 → clean/{stem}.reconstructed.md
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config.settings import Settings  # noqa: E402
from core.llm.deepseek import DeepSeekProvider  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("llm_md_reconstruct")

HANDOFF_DIR = pathlib.Path("DATA/raw_data/handoff_v2")
RAW_DIR = HANDOFF_DIR / "raw"
CLEAN_DIR = HANDOFF_DIR / "clean"
MODEL_DEFAULT = "deepseek-v4-flash"
MAX_WORKERS = 5  # rule1.md：并发上限 3~5，防 429

# ── rule.md 第三节约定的完整 System Prompt（原封不动）──────
SYSTEM_PROMPT = """你是一个专业的 Markdown 文档结构重构引擎。你的唯一任务是修复输入文本中的排版缺陷和层级错乱。

【第一铁律：文本绝对零损耗 (Textual Fidelity)】
清洗过程为无损排版修整，严禁增删、改写、润色或替换原文中的任何一个汉字、数字、字母与标点符号！

【第二铁律：版面与段落重构规范 (Layout & Paragraph Rules)】
1. 彻底废除固定字数硬换行：严禁使用任何固定字数切行逻辑（如每行满 X 字敲回车）。
2. 单段单行原则：在 Markdown 文件内部，一个完整的自然段落必须保持为一条连续无 \\n 的单行长字符串，由 Markdown 渲染器自行适应幅宽。
3. 段落分隔标准：自然段与自然段之间，统一使用双换行符（\\n\\n）进行分隔。
4. 严禁断头行：严禁出现孤字行、断头行（如上一行结尾为“...招标人和中标人签”，下一行单独留“订合同。”）。只要行尾非终端标点，必须无条件与后文合并为同一自然段。

【第三铁律：标题与层级识别规则 (Heading & Hierarchy Rules)】
1. 假标题防误判护栏（硬性排除）：
   - 排除小数与金额：正则表达式或语义识别时，必须硬性排除带小数点的数值（如 2.2）。含 "2.2亿元"、"500万元"、"3000万元" 等金额/数值/年份的句子，绝对禁止识别或抬升为 # 标题，必须恢复为普通正文。
   - 排除动词与叙述句：包含“按约履行”、“经审理认为”、“判定如下”、“后，铺道工程公司”等长句正文，严禁带有 # 标号，必须彻底降级剥离 # 恢复为普通段落。
2. 断裂标题吞噬缝合：若标题行（如以 （一）、（二）、Q21.、1. 开头）结尾缺乏终端标点（？/！/。/：/”），且下一行属于该标题的语义延续（例如上一行止于“...能否”，下一行为“成立的问题。”），必须无条件将下一行拼回同一标题行中。
3. 标准 Markdown 层级映射：
   - # ：全书书名 / 法律法规名称（全篇唯一顶级根）。
   - ## ：篇、章、大型主题（如 第二章 招标）。
   - ### ：节、具体条款、数字问答题目（如 ### Q21. ...）。
   - 正文：剥离所有非必要 Markdown 标题标记，恢复为普通文本段落。

【第四铁律：案例、引文与特殊元素处理 (Special Elements)】
1. 案例与法条独立提行：遇到“相关案例X-X”、“《法释〔2020〕25号》”等独立标签时，必须在其前后保留标准段落分隔（\\n\\n），不得与后文的具体日期或正文叙述（如“2011年4月21日，某县...”）无间隙挤进同一行。
2. 表格与结构化保留：遇到标准表格区域，保持原有表格语法或逐行独立呈现，不得强行将表格单元格当作普通正文段落拼接到上一行。

请直接输出修复后的 Markdown 文本，绝对不要包含任何 Markdown 标记块声明（如 ```markdown）之外的任何开场白、解释或总结说明。"""

_PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:\s*\d+\s*-->\s*$")
_TERMINAL = "。！？…；：”』」）"
_MD_NOISE_RE = re.compile(r"[#>`*\n\r\t ]")


@dataclass
class Book:
    stem: str
    raw_path: pathlib.Path
    pages: List[str] = field(default_factory=list)


def discover_books() -> List[Book]:
    return [
        Book(stem=rp.name[: -len(".raw.md")], raw_path=rp)
        for rp in sorted(RAW_DIR.glob("*.raw.md"))
    ]


def parse_pages(text: str) -> List[Tuple[Optional[int], str]]:
    out: List[Tuple[Optional[int], str]] = []
    cur_no: Optional[int] = None
    cur: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m:
            if cur:
                out.append((cur_no, "\n".join(cur)))
            cur_no, cur = int(m.group(1)), []
        else:
            cur.append(line)
    if cur:
        out.append((cur_no, "\n".join(cur)))
    return out


def strip_assembly_markers(text: str) -> str:
    lines = [l for l in text.split("\n") if not _PAGE_MARKER_RE.match(l)]
    return "\n".join(lines)


def split_paragraph_blocks(text: str) -> List[str]:
    return [b for b in (b.strip() for b in re.split(r"\n\s*\n", text)) if b]


def build_chunks(text: str, *, target: int = 3500, cap: int = 4200) -> List[str]:
    """按自然段（\n\n）界切 ≈target 字块；起点/终点都在段界，绝不切段内/行内。"""
    blocks = split_paragraph_blocks(text)
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for b in blocks:
        if cur_len + len(b) <= cap or not cur:
            cur.append(b)
            cur_len += len(b)
        else:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [b], len(b)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def make_llm(model: str):
    settings = Settings()
    if not settings.llm_api_key:
        raise RuntimeError("缺少 LLM_API_KEY / DEEPSEEK_API_KEY")
    return DeepSeekProvider(settings).build(model=model, temperature=0.0)


def _strip_fence(content: str) -> str:
    lines = content.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip("\n") + "\n"


def verify_fidelity(orig_text: str, clean_text: str) -> float:
    """rule1.md：Counter 字符频次毫秒级忠实度。

    先剔除 Markdown 标记符号与空白（与 rule.md 验收口径一致：剔除标记后
    核心汉字/数字不漏字），再按字符频次交叠占比计。
    """
    o = _MD_NOISE_RE.sub("", orig_text)
    c = _MD_NOISE_RE.sub("", clean_text)
    if not o:
        return 1.0
    co, cc = Counter(o), Counter(c)
    overlap = sum((co & cc).values())
    return overlap / max(len(o), 1)


# 线程本地 LLM（并发下各自持连接，避免共享 client 竞态）
_tls = threading.local()


def _thread_llm(model: str):
    llm = getattr(_tls, "llm", None)
    if llm is None:
        llm = make_llm(model)
        _tls.llm = llm
    return llm


def clean_task(
    model: str, task_id: int, text: str, retries: int, min_fid: float
):
    """单块重构：返回 (text, api_sec, verify_sec, fidelity, error)。

    fidelity 门控：内容丢失超过阈值（fid < min_fid）视为可疑截断/篡改 → 重发，
    保留多轮中 fidelity 最高的一次（rule.md 唯一硬红线：文本字数忠实度 ≥96%）。
    """
    llm = _thread_llm(model)
    last_err: Optional[Exception] = None
    best: Optional[Tuple[str, float, float, float]] = None
    for attempt in range(retries + 1):
        try:
            t0 = time.perf_counter()
            msg = llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT),
                 HumanMessage(content=text)]
            )
            api_sec = time.perf_counter() - t0
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            clean = _strip_fence(content)
            t1 = time.perf_counter()
            fid = verify_fidelity(text, clean)
            ver_sec = time.perf_counter() - t1
            if best is None or fid > best[3]:
                best = (clean, api_sec, ver_sec, fid)
            if fid >= min_fid:
                return best
            log.warning(
                "task#%d 忠实度 %.4f < %.2f（疑似截断），重发…",
                task_id, fid, min_fid,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("task#%d try%d 失败: %s", task_id, attempt + 1, exc)
        if attempt < retries:
            time.sleep(2 + attempt)
    if best is not None:
        log.warning("task#%d 多次重发仍低于阈值，取最高 fidelity=%.4f",
                    task_id, best[3])
        return best
    return "", 0.0, 0.0, 0.0, str(last_err)


def run_concurrent(
    tasks: Sequence[Tuple[int, str, str]],
    *,
    model: str,
    retries: int = 2,
    min_fid: float = 0.96,
    log_prefix: str = "Page",
) -> Dict[int, Tuple[str, float, float, float]]:
    """并发执行 tasks=[(id,label,text)]；返回 {id:(clean,api_sec,ver_sec,fid)}。

    顺序组装由调用方用返回的 id 升序完成（rule1.md 护栏 3）。
    """
    results: Dict[int, Tuple[str, float, float, float]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {
            ex.submit(clean_task, model, tid, text, retries, min_fid): (tid, label)
            for (tid, label, text) in tasks
        }
        for fut in as_completed(futs):
            tid, label = futs[fut]
            clean, api_sec, ver_sec, fid, err = fut.result()
            if err:
                log.error("%s#%s 失败: %s", log_prefix, label, err)
                continue
            results[tid] = (clean, api_sec, ver_sec, fid)
            log.info(
                "[%s %s] API响应耗时: %.2fs | 本地校验耗时: %.4fs | 忠实度: %.4f",
                log_prefix, label, api_sec, ver_sec, fid,
            )
    return results


# ── 验收指标（线性，毫秒级；仅供报表，不做清洗加工）────────


def headless_rate(output: str) -> float:
    """断头行率：仅当"同一自然段被拆行"时计（即无空行分隔、前行非终端收尾）。

    单段单行 + \n\n 分隔的合法输出里，凡以空行结尾的段落都不计入。
    """
    lines = output.split("\n")
    n = len(lines)
    bad = 0
    body = 0
    for i in range(n):
        cur = lines[i]
        if not cur.strip() or cur.lstrip().startswith("#"):
            continue
        body += 1
        if cur.rstrip()[-1] in _TERMINAL:
            continue  # 行尾终端 → 本段完整
        nxt = lines[i + 1] if i + 1 < n else None
        if nxt is None or not nxt.strip():
            continue  # 段落以空行/EOF 收尾（合法）
        if nxt.lstrip().startswith("#"):
            continue  # 下段是标题（合法切换）
        bad += 1  # 无空行且非标题延续 → 前行被硬断，续行仍在后
    return bad / max(1, body)


def heading_stats(output: str) -> Tuple[int, float, float]:
    """返回 (标题数, 假标题率, 内容标题完整闭合率)。仅参考、不触发重试。

    “标题完整闭合”只针对**可被腰斩的内容题**：Q/数字问/（一）/一、 这类标题在
    还原后应当自带收尾符（？/。/：等）；若仍不以收尾符结束 = 仍被腰斩。
    章/条/书名标题（无需句读收尾）不参与该分母。
    """
    lines = output.split("\n")
    heads = [i for i, l in enumerate(lines) if l.lstrip().startswith("#")]
    false = 0
    content = 0
    broken = 0
    for idx in heads:
        t = re.sub(r"^#+\s*", "", lines[idx]).strip()
        if re.search(r"\d+(\.\d+)?\s*(亿元|万元|元|％|%)", t):
            false += 1
        elif re.search(r"\s+\d{1,3}$", t):
            false += 1
        if re.match(r"^(Q\s*\d|\d{1,3}\s*[.、．]|（[一二三\d]+）|[一二三]+、)", t):
            content += 1
            if not t.endswith(tuple(_TERMINAL + "？?")):
                broken += 1
    nh = len(heads)
    close = 1.0 - broken / max(1, content) if content else 1.0
    return nh, false / max(1, nh), close


# ── 子命令 ─────────────────────────────────────────────────


def cmd_ping(args) -> int:
    log.info("ping: model=%s base_url=%s", args.model,
             Settings().llm_base_url or "default")
    llm = make_llm(args.model)
    msg = llm.invoke(
        [SystemMessage(content=SYSTEM_PROMPT),
         HumanMessage(content="这是一行中文测试。\n请原样回显第一句。\n")]
    )
    print("PING OK:", repr(str(msg.content)[:80]))
    return 0


def cmd_pilot(args) -> int:
    rng = random.Random(args.seed)
    books = discover_books()
    if args.books:
        books = [books[int(x)] for x in args.books.split(",")]

    per_book: List[List[str]] = []
    for b in books:
        raw = b.raw_path.read_text(encoding="utf-8")
        pages = [
            txt.strip()
            for (_, txt) in parse_pages(raw)
            if txt.strip()
        ]
        per_book.append(pages)
        log.info("%s: %d 页", b.stem[:14], len(pages))

    total = sum(len(p) for p in per_book)
    counts = [round(args.n * len(p) / total) if p else 0 for p in per_book]
    diff = args.n - sum(counts)
    for k in range(max(0, diff)):
        counts[rng.randrange(len(counts))] += 1
    while diff < 0:
        k = max(range(len(counts)), key=lambda i: counts[i])
        if counts[k] > 0:
            counts[k] -= 1
            diff += 1
        else:
            break

    # (order_key, label, text) —— 单页默认一个请求；页超过 ~3500 时按 \n\n 切块为
    # 多个子请求（rule.md 3000~4000 分块；同页子请求连续编号、按 order 顺序重组，
    # 验收口径与整页一致，避免大页整页直传拖慢生成）
    tasks: List[Tuple[int, str, str]] = []
    order = 0
    n_sample = 0
    for bi, pgs in enumerate(per_book):
        for pi in sorted(rng.sample(range(len(pgs)), min(counts[bi], len(pgs)))):
            n_sample += 1
            page_text = pgs[pi]
            parts = build_chunks(
                page_text, target=args.chunk, cap=args.chunk + 700
            )
            for k, part in enumerate(parts):
                order += 1
                label = f"{books[bi].stem[:6]}-p{pi}"
                if len(parts) > 1:
                    label += f"#{k + 1}"
                tasks.append((order, label, part))
    log.info("试点 %d 页 → %d 个请求（并发 %d）",
             n_sample, len(tasks), MAX_WORKERS)

    results = run_concurrent(
        tasks, model=args.model, retries=args.retry,
        min_fid=args.fid, log_prefix="Page"
    )

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 按 order_key 升序一次性组装落盘（防页码错乱）
    sorted_ids = sorted(tasks, key=lambda t: t[0])
    with out_path.open("w", encoding="utf-8") as fh:
        for tid, _, _ in sorted_ids:
            if tid in results:
                fh.write(results[tid][0])
                fh.write("\n")
    print(f"已按序落盘: {out_path}")

    fids = [results[t][3] for t, _, _ in sorted_ids if t in results]
    done_n = len(fids)
    print("\n===== PILOT 验收报表 =====")
    print(f"模型: {args.model}   完成: {done_n}/{len(tasks)}")
    agg = sum(fids) / done_n if done_n else float("nan")
    hl = headless_rate(out_path.read_text(encoding="utf-8")) if done_n else 1.0
    nhead, fh_rate, close = heading_stats(
        out_path.read_text(encoding="utf-8")
    ) if done_n else (0, 1.0, 0.0)
    print(f"平均 文本忠实度 = {agg*100:.2f}% (>=96% 通过)")
    print(f"全局 断头行率   = {hl*100:.2f}% (<=1% 通过)")
    print(f"全局 假标题率   = {fh_rate*100:.2f}% (<=3% 参考)")
    print(f"全局 标题闭合率 = {close*100:.2f}% (>=95% 参考)  标题数={nhead}")
    print(f"最低单页 忠实度 = {min(fids)*100:.2f}%" if fids else "n/a")
    ok = (done_n == len(tasks)) and agg >= 0.96 and hl <= 0.01
    print("PILOT RESULT:", "PASS -> 可继续全量" if ok else "FAIL -> 先排查再全量")
    return 0 if ok else 2


def cmd_full(args) -> int:
    books = discover_books()
    for b in books:
        raw = b.raw_path.read_text(encoding="utf-8")
        text = strip_assembly_markers(raw)
        chunks = build_chunks(text, target=args.chunk)
        log.info("%s: %d 块 (~%d 字符)", b.stem[:14], len(chunks), len(text))
        tasks = [
            (i, f"{b.stem[:6]}-c{i + 1}", c)
            for i, c in enumerate(chunks)
        ]
        results = run_concurrent(
            tasks, model=args.model, retries=args.retry,
            min_fid=args.fid, log_prefix="Chunk"
        )
        out_path = CLEAN_DIR / f"{b.stem}.reconstructed.md"
        missing = [t[0] for t in tasks if t[0] not in results]
        if missing:
            log.error("%s 仍有 %d 块失败，未落盘（改天 --retry 加大或重跑续接）",
                      b.stem[:14], len(missing))
            continue
        with out_path.open("w", encoding="utf-8") as fh:
            for tid, _, _ in sorted(tasks, key=lambda t: t[0]):
                fh.write(results[tid][0])
                fh.write("\n")
        log.info("完成 %s → %s (%d 块)", b.stem, out_path, len(chunks))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Markdown 结构重构引擎（rule.md+rule1.md）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ping")
    p.add_argument("--model", default=MODEL_DEFAULT)

    pp = sub.add_parser("pilot")
    pp.add_argument("--books", default=None)
    pp.add_argument("--n", type=int, default=50)
    pp.add_argument("--seed", type=int, default=20260905)
    pp.add_argument("--chunk", type=int, default=3500)
    pp.add_argument("--fid", type=float, default=0.96)
    pp.add_argument("--retry", type=int, default=2)
    pp.add_argument("--out", default=str(HANDOFF_DIR / "clean" / "_pilot_result.md"))
    pp.add_argument("--model", default=MODEL_DEFAULT)

    pf = sub.add_parser("full")
    pf.add_argument("--chunk", type=int, default=3500)
    pf.add_argument("--fid", type=float, default=0.96)
    pf.add_argument("--retry", type=int, default=2)
    pf.add_argument("--model", default=MODEL_DEFAULT)

    args = ap.parse_args(argv)
    if args.cmd == "ping":
        return cmd_ping(args)
    if args.cmd == "pilot":
        return cmd_pilot(args)
    if args.cmd == "full":
        return cmd_full(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
