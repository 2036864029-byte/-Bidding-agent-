"""E4-1 线上 trace 采样回灌金标（闭环第一步）。

从 Langfuse 自托管实例的 ClickHouse（HTTP 8123）**只读**拉取近期 trace，按规则
标记异常，产出「金标标注候选」；人工复核（`review_status=reviewed`）后经
`merge_into_golden` 追加进 `eval/agent_golden_set.json`。

数据模型（实测 Langfuse v4 events_only）：
  - 根 span：`parent_span_id = ''`（OTLP 不写 `is_app_root`）；根 span 无 input/output。
  - 问题 / 答案：落在 **节点 span** 上（`core.errors.fallback.with_fallback` 写入
    `langfuse.observation.input/output`，Langfuse 映射到 events_full 的 input/output）。
  - 工具：优先取 `retrieval.*` span；缺失时按节点 `ztb.branch` 推断
    （price_inquiry→price_query / knowledge_qa→kb_search_tool）。

异常判据（任一命中即入选）：
  ① error         任一 span level=ERROR 或 status_message 非空
  ② empty_answer  节点答案为空
  ③ slow          端到端耗时 > slow_ms（默认 15000）
  ④ fallback      答案命中降级话术 / 节点 branch=fallback

候选只写到 `test_report/`（gitignored）；只有复核过的才进金标集。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

__all__ = [
    "ClickHouseCfg", "load_ch_cfg", "fetch_root_traces", "fetch_trace_events",
    "extract_question", "extract_answer", "extract_branch", "observed_tools",
    "flag_root", "build_candidate", "sample_candidates", "merge_into_golden",
    "quality_scores",
]

FLAG_ERROR = "error"
FLAG_EMPTY = "empty_answer"
FLAG_SLOW = "slow"
FLAG_FALLBACK = "fallback"
FLAG_MULTI_ATOM = "multi_atom_single_branch"

KB_TOOL = "kb_search_tool"
PRICE_TOOL = "price_query"

_FALLBACK_HINTS = ("暂时无法", "抱歉", "系统繁忙", "服务异常", "请稍后")

#: 多原子信号词（问题含多个需分别查证的事项）——启发式，命中且仅走单分支时入选
_MULTI_HINTS = ("同时", "另外", "此外", "分别说明", "分别", "其一", "其二", "并且", "以及")

#: 跨域信号：走了 price 分支但问题要法规解释 → 疑漏 kb（反之太噪声，不做）
_LEGAL_HINTS = ("法规", "法律", "规定", "依据", "法律责任", "义务", "原则", "程序", "条例", "办法")

#: 单分支（非 deep_agent）——多原子问题落到这些分支即可疑
_SINGLE_BRANCHES = {"knowledge_qa", "price_inquiry"}

#: 节点 branch → 该分支应调用的工具（retrieval span 缺失时的推断来源）
_BRANCH_TOOLS = {
    "price_inquiry": [PRICE_TOOL],
    "knowledge_qa": [KB_TOOL],
}


@dataclass(frozen=True)
class ClickHouseCfg:
    host: str = "127.0.0.1"
    port: int = 8123
    user: str = "clickhouse"
    password: str = ""
    timeout: int = 30


def load_ch_cfg(deploy_env: Path | str | None = None) -> ClickHouseCfg:
    """从 Langfuse 部署目录的 .env 读取 ClickHouse 凭据（缺省回落本地默认）。"""
    cfg = ClickHouseCfg()
    env = Path(deploy_env) if deploy_env else (
        Path(__file__).resolve().parent.parent / "deploy" / "langfuse" / ".env")
    if not env.exists():
        return cfg
    vals: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return ClickHouseCfg(
        host=vals.get("CLICKHOUSE_HOST", cfg.host),
        port=int(vals.get("CLICKHOUSE_HTTP_PORT", cfg.port)),
        user=vals.get("CLICKHOUSE_USER", cfg.user),
        password=vals.get("CLICKHOUSE_PASSWORD", cfg.password),
        timeout=cfg.timeout,
    )


def _ch_query(sql: str, cfg: ClickHouseCfg) -> list[dict]:
    """ClickHouse HTTP 接口只读查询（FORMAT JSON）。"""
    import requests

    resp = requests.post(
        f"http://{cfg.host}:{cfg.port}/",
        params={"query": sql.rstrip().rstrip(";") + " FORMAT JSON"},
        auth=(cfg.user, cfg.password),
        timeout=cfg.timeout,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_root_traces(cfg: ClickHouseCfg, *, since_hours: int = 48,
                      limit: int = 200, root_name: str = "") -> list[dict]:
    """近 N 小时的应用根 span（parent_span_id 为空 = 一次用户请求）。

    root_name 非空时按其前缀过滤（如 'POST /chat'；实测 OTLP 不写 is_app_root）。
    """
    name_filter = f" AND name LIKE '{root_name}%'" if root_name else ""
    sql = f"""
    SELECT trace_id, name AS trace_name, start_time, end_time,
           level, status_message,
           dateDiff('millisecond', start_time, end_time) AS latency_ms
    FROM events_full
    WHERE parent_span_id = '' AND start_time >= now() - INTERVAL {int(since_hours)} HOUR{name_filter}
    ORDER BY start_time DESC
    LIMIT {int(limit)}
    """
    return _ch_query(sql, cfg)


def fetch_trace_events(cfg: ClickHouseCfg, trace_id: str) -> list[dict]:
    """一条 trace 下的全部 span（按开始时间）；含节点 input/output 与 branch。"""
    sql = f"""
    SELECT name, type, level, status_message, input, output,
           dateDiff('millisecond', start_time, end_time) AS latency_ms,
           arrayElement(metadata_values, indexOf(metadata_names, 'attributes.ztb.branch')) AS branch,
           arrayElement(metadata_values, indexOf(metadata_names, 'attributes.ztb.node')) AS node
    FROM events_full
    WHERE trace_id = '{trace_id}'
    ORDER BY start_time ASC
    """
    return _ch_query(sql, cfg)


def _clean(text: Any) -> str:
    return (text or "").strip() if isinstance(text, str) else ""


def extract_question(events: Sequence[dict]) -> str:
    """问题：首个非空 input（节点 span 写入）。"""
    for e in events:
        q = _clean(e.get("input"))
        if q:
            return q
    return ""


def extract_answer(events: Sequence[dict]) -> str:
    """答案：末个非空 output（产生回答的节点 span 写入）。"""
    ans = ""
    for e in events:
        o = _clean(e.get("output"))
        if o:
            ans = o
    return ans


def extract_branch(events: Sequence[dict]) -> str:
    """业务分支：首个非空且非 '?' 的 ztb.branch。"""
    for e in events:
        b = _clean(e.get("branch"))
        if b and b != "?":
            return b
    return ""


def observed_tools(events: Sequence[dict]) -> list[str]:
    """实际调用的工具序列。

    优先取 `retrieval.*` span（检索层埋点）；缺失时按节点 branch 推断
    （price_inquiry→price_query / knowledge_qa→kb_search_tool）。按时间先后，去连续重复。
    """
    tools: list[str] = []
    for e in events:
        name = str(e.get("name", ""))
        if "kb_search" in name:
            tools.append(KB_TOOL)
        elif "price_query" in name:
            tools.append(PRICE_TOOL)
    if not tools:  # 无检索 span → 按 branch 推断
        branch = extract_branch(events)
        tools = list(_BRANCH_TOOLS.get(branch, []))
    out: list[str] = []
    for t in tools:
        if not out or out[-1] != t:
            out.append(t)
    return out


def flag_root(root: dict, events: Sequence[dict], *, slow_ms: int = 15000) -> list[str]:
    """按规则标记根 trace 的异常（返回命中的 flag 列表，空 = 正常）。"""
    flags: list[str] = []
    question = extract_question(events)
    answer = extract_answer(events)
    branch = extract_branch(events)
    if not answer:
        flags.append(FLAG_EMPTY)
    if any(str(e.get("level", "")).upper() == "ERROR" or _clean(e.get("status_message"))
           for e in events):
        flags.append(FLAG_ERROR)
    if any(h in answer for h in _FALLBACK_HINTS) or branch == "fallback":
        flags.append(FLAG_FALLBACK)
    if branch in _SINGLE_BRANCHES and (
        any(h in question for h in _MULTI_HINTS)
        or (branch == "price_inquiry" and any(h in question for h in _LEGAL_HINTS))
    ):
        flags.append(FLAG_MULTI_ATOM)   # 多源问题仅走单分支 → 疑漏查（E5 路由面）
    lat = root.get("latency_ms")
    if isinstance(lat, (int, float)) and lat > slow_ms:
        flags.append(FLAG_SLOW)
    return flags


def _propose_query_type(tools: Sequence[str]) -> str:
    s = set(tools)
    if {KB_TOOL, PRICE_TOOL} <= s:
        return "mixed"
    if s == {PRICE_TOOL}:
        return "price"
    return "kb_multi_hop"


def quality_scores(root: dict, events: Sequence[dict]) -> list[dict]:
    """确定性质量信号（零额外 LLM），供回写 Langfuse Scores（E4-3b）。

    返回 [{name, value, comment}]：
      - answered              答案非空且非降级 = 1，否则 0
      - single_branch_complex 多源问题仅走单分支 = 1（E5 路由关注点），否则 0
    """
    flags = flag_root(root, events)
    answered = 0.0 if (FLAG_EMPTY in flags or FLAG_FALLBACK in flags) else 1.0
    multi = FLAG_MULTI_ATOM in flags
    return [
        {"name": "answered", "value": answered,
         "comment": f"flags={','.join(flags) or 'none'}"},
        {"name": "single_branch_complex", "value": 1.0 if multi else 0.0,
         "comment": "多源问题仅走单分支（E5 路由关注点）" if multi else ""},
    ]


def build_candidate(root: dict, events: Sequence[dict], flags: Sequence[str]) -> dict:
    """根 trace + 子 span → 金标标注候选（expected_* 为**待复核提案**）。"""
    tools = observed_tools(events)
    question = extract_question(events)
    branch = extract_branch(events) or "?"
    return {
        "q": question,
        "query_type": _propose_query_type(tools),
        "expected_sequence": tools,
        "expected_tools": sorted(set(tools)),
        "key_entities": [],          # 待人工补
        "rationale": f"trace 回灌命中 [{', '.join(flags)}]；分支 {branch}；"
                     f"实测工具序列 {tools or '（无检索）'}；耗时 {root.get('latency_ms')}ms。"
                     f"expected_* 为提案，需复核。",
        "source": "trace_feedback",
        "review_status": "pending",  # 复核后改 reviewed 才能 merge
        "trace_ref": {
            "trace_id": root.get("trace_id"),
            "branch": branch,
            "flagged": list(flags),
            "latency_ms": root.get("latency_ms"),
        },
    }


def sample_candidates(cfg: ClickHouseCfg, *, since_hours: int = 48, limit: int = 200,
                      slow_ms: int = 15000, root_name: str = "POST /chat",
                      include_clean: bool = False) -> list[dict]:
    """采样：拉根 trace → 标记异常 → 生成候选（默认仅返回命中异常的）。"""
    roots = fetch_root_traces(cfg, since_hours=since_hours, limit=limit, root_name=root_name)
    out: list[dict] = []
    for root in roots:
        events = fetch_trace_events(cfg, root["trace_id"])
        if not extract_question(events):
            continue
        flags = flag_root(root, events, slow_ms=slow_ms)
        if flags or include_clean:
            out.append(build_candidate(root, events, flags or ["clean"]))
    return out


def merge_into_golden(candidates: Sequence[dict], golden_path: Path | str, *,
                      require_reviewed: bool = True) -> dict:
    """把复核过的候选追加进金标集（按问题去重；未复核的默认拒绝）。

    只写入金标 schema 字段，丢弃 trace_ref / review_status 等采样元数据。
    """
    golden_path = Path(golden_path)
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    items = data["items"]
    existing = {it["q"] for it in items}
    keep = ("q", "query_type", "expected_sequence", "expected_tools",
            "key_entities", "rationale", "source")
    added: list[str] = []
    skipped: list[dict] = []
    for c in candidates:
        q = _clean(c.get("q"))
        if require_reviewed and c.get("review_status") != "reviewed":
            skipped.append({"q": q[:40], "reason": "not_reviewed"})
            continue
        if not q or q in existing:
            skipped.append({"q": q[:40], "reason": "empty_or_duplicate"})
            continue
        items.append({k: c[k] for k in keep if k in c})
        existing.add(q)
        added.append(q)
    if added:
        golden_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"added": len(added), "skipped": len(skipped),
            "added_q": added, "skipped_detail": skipped}
