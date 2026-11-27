"""channels.feishu.render — AgentReply（/chat 响应）→ 飞书卡片 / 文本消息。

卡片一律走 **interactive（卡片 JSON 1.0）**：消息 API 会静默拒收 schema 2.0
（2026-09-23 实测，见执行进度 4B 记录）。P2 起卡片按分支组织：

- ``kb_answer`` **不带 header**（回答顶格，用户反馈 09-25），引用区放进**折叠面板**
  （``collapsible_panel``，官方组件、客户端 ≥7.9）默认收起——层次由"回答是主角、来源可展开"体现；
  其余分支仍带 header（分支标识 + 主题色），底部按需给耗时 note；
- ``price_records`` → 原生 **table** 组件（v1 ``tag: table``，手机端内置翻页）。
  ``answer`` 只保留引导句/结语——竖线分隔的列表行改由表格承载，避免同批数据出现两次；
  单条记录是叙述性正文，不加表格（避免冗余）；
- ``deep_agent`` → 正文下「思维链」note（工具 → 结果条数摘要；交互式折叠留 P3）；
- ``doc_qa`` → 正文下状态 note（success / failed / no_doc_attached）；
- ``safety``/``fallback`` → 提示卡 header + 友好文案；``clarify`` 候选列序号（按钮留 P3）。

仅「后端不可用/异常」这类通道层错误回纯文本（handler 直接给 str）。
引用块走本模块 ``_citations_block``（结构对齐
``core/protocol/renderers/cli.render_citations``，原文片段更短、适配飞书移动端）。

表格组件字段按飞书官方文档（卡片 JSON 1.0）实现：``columns`` 最多 50 列、
``page_size`` ∈ [1,10]、不可嵌套（只能放卡片根节点）、单卡最多 5 个表格。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: error_code → 飞书友好文案（core.errors.codes.ErrorCode 的常用子集；
#: 未知码回退"抱歉…+code"）。
_ERROR_TEXT: Dict[str, str] = {
    "biz_1002": "没找到相关信息，换个问法试试？",
    "biz_1003": "没找到相关信息，换个问法试试？",
    "biz_1005": "这个功能目前不可用，请换个问题。",
    "sys_2002": "暂时没能理解您的问题，请换个说法。",
    "sys_2003": "请求太频繁，请稍后再试。",
    "sys_2004": "系统繁忙，请稍后再试。",
    "sys_2500": "处理出错，请稍后重试。",
    "model_3001": "模型服务暂时不可用，请稍后再试。",
    "model_3002": "向量服务暂时不可用，请稍后再试。",
    "dep_4001": "知识库服务暂时不可用，请稍后再试。",
    "dep_4002": "数据服务暂时不可用，请稍后再试。",
}

#: 正文单元素上限（卡片内容有体积约束，超长截断而非失败）。
_MAX_ANSWER_CHARS = 2000
#: 引用"原文"片段截断（比 CLI 渲染短一点，飞书移动端省空间）。
_CITATION_SNIPPET_CHARS = 120
#: price_records 表格：单表最多展示的行数（超过截断并提示；表格组件自身按 page_size 翻页）。
_MAX_TABLE_ROWS = 50
#: 表格最多列数（飞书上限 50，见官方文档；本项目单记录 ≤12 字段，远低于上限）。
_MAX_TABLE_COLS = 50
#: 思维链 note 最多展示的步骤数。
_TRACE_STEPS_SHOWN = 8
#: 金额列（records 机器键）→ 原生 number 列 + ¥ / 千分位 / 2 位小数。
_AMOUNT_KEYS = {"winning_amount"}
#: kb_answer 引用区用折叠面板（官方组件，飞书客户端需 **≥7.9**，低版本显示
#: "请升级客户端"占位图）。False → 回退 hr + 引用块（旧行为，兼容低版本）。
_CITATIONS_COLLAPSIBLE = True

#: reply_type → 卡片 header（分支标识 + 主题色；色值取卡片 JSON 1.0 header 枚举）。
_HEADER_TITLES: Dict[str, Tuple[str, str]] = {
    "kb_answer": ("📚 知识问答", "blue"),
    "price_records": ("📊 价格/标讯查询", "blue"),
    "chat": ("💬 对话", "grey"),
    "doc_qa": ("📄 文档问答", "blue"),
    "deep_agent": ("🧠 深度检索", "turquoise"),
    "safety": ("⚠️ 内容提示", "red"),
    "fallback": ("😅 出错了", "orange"),
    "clarify": ("🤔 需要确认", "orange"),
}


def _error_message(error_code: str, answer: str) -> str:
    text = _ERROR_TEXT.get(str(error_code))
    if text:
        return text
    if answer:
        return f"抱歉，处理遇到问题（{error_code}）：\n{answer}"
    return f"抱歉，处理遇到问题（{error_code}），请稍后重试。"


def _clip(text: str, n: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _citations_lines(citations: List[Dict[str, Any]]) -> List[str]:
    """逐来源引用行（不带"共 N 条"标题——标题由折叠面板/文本块头承载）。"""
    lines: List[str] = []
    for c in citations:
        idx = c.get("context_index", "")
        doc = c.get("doc_name") or "未知文档"
        chapter = c.get("chapter") or ""
        header = f"【来源{idx}】{doc}"
        if chapter:
            header += f"｜{chapter}"
        lines.append(header)
        text = _clip(str(c.get("text") or ""), _CITATION_SNIPPET_CHARS)
        if text:
            lines.append(f"   {text}")
        lines.append("")
    return lines


def _citations_block(citations: List[Dict[str, Any]]) -> str:
    """引用来源文本块（文本消息降级用；折叠面板场景用 _citations_lines）。"""
    if not citations:
        return ""
    lines: List[str] = [f"📚 引用来源（共 {len(citations)} 条）", ""]
    lines += _citations_lines(citations)
    return "\n".join(lines).rstrip()


def _collapsible_panel(
    title: str,
    elements: List[Dict[str, Any]],
    *,
    expanded: bool = False,
    background_color: str = "grey",
) -> Dict[str, Any]:
    """v1 折叠面板（官方组件，飞书客户端 ≥7.9；结构对齐官方示例）。

    标题默认带展开/收起箭头；``background_color`` 给内容区一层底色
    （用户想要的"色块区分"，low 版本才有的能力）。
    """
    panel: Dict[str, Any] = {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "vertical_align": "center",
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "color": "",
                "size": "16px 16px",
            },
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    }
    if background_color:
        panel["background_color"] = background_color
    return panel


def _note_element(content: str) -> Dict[str, Any]:
    """小字号说明块（表格截断提示 / 思维链 / 状态 / 耗时）。"""
    return {"tag": "note", "elements": [{"tag": "lark_md", "content": content}]}


def _header_for(reply_type: str) -> Dict[str, Any]:
    title, template = _HEADER_TITLES.get(reply_type, ("招小智", "blue"))
    return {"template": template, "title": {"tag": "plain_text", "content": title}}


# ── price_records → 原生 table 组件 ─────────────────────────────


def _to_number(value: Any) -> Optional[float]:
    """尽力把单元格值转成 float；无法解析（带单位/中文/空）→ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _column_is_number(records: List[dict], key: str) -> bool:
    """列内所有非空值都能解析为数字 → 整列按 number 渲染（任一失败回退 text）。"""
    seen = False
    for rec in records:
        if not isinstance(rec, dict):
            continue
        value = rec.get(key)
        if value is None or value == "":
            continue
        seen = True
        if _to_number(value) is None:
            return False
    return seen


def _table_element(data: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """``data.records`` / ``data.columns`` → v1 table 组件。

    返回 ``(element, truncated)``：records/columns 缺失或为空 → ``(None, False)``
    （调用方走纯文本）。行值 None/空 → 省略该单元格；金额列（``_AMOUNT_KEYS``）
    整列数值化（任一值不可解析则整列回退 text）+ ¥/千分位格式；超过
    ``_MAX_TABLE_ROWS`` 截断并置 truncated（调用方补提示 note）。
    """
    if not isinstance(data, dict):
        return None, False
    records = data.get("records")
    columns = data.get("columns")
    if not isinstance(records, list) or not isinstance(columns, list):
        return None, False
    records = [r for r in records if isinstance(r, dict)]
    if not records or not columns:
        return None, False

    table_columns: List[Dict[str, Any]] = []
    amount_keys: set = set()
    for col in columns[:_MAX_TABLE_COLS]:
        if not isinstance(col, dict):
            continue
        key = col.get("key")
        if not key:
            continue
        spec: Dict[str, Any] = {
            "name": key,
            "display_name": col.get("label") or "",
            "data_type": "text",
            "horizontal_align": "left",
            "vertical_align": "top",
        }
        if key in _AMOUNT_KEYS and _column_is_number(records, key):
            spec["data_type"] = "number"
            spec["format"] = {"symbol": "¥", "precision": 2, "separator": True}
            spec["horizontal_align"] = "right"
            amount_keys.add(key)
        table_columns.append(spec)

    truncated = len(records) > _MAX_TABLE_ROWS
    rows: List[Dict[str, Any]] = []
    for rec in records[:_MAX_TABLE_ROWS]:
        row: Dict[str, Any] = {}
        for col in table_columns:
            key = col["name"]
            value = rec.get(key)
            if value is None or value == "":
                continue
            if key in amount_keys:
                number = _to_number(value)
                row[key] = number if number is not None else value
            else:
                row[key] = str(value)
        rows.append(row)

    return {
        "tag": "table",
        "page_size": 10 if len(rows) > 10 else max(5, len(rows)),
        "columns": table_columns,
        "rows": rows,
    }, truncated


def _strip_table_lines(answer: str) -> str:
    """去掉竖线分隔的列表行（price_records answer 里的数据行），保留引导句/结语。"""
    lines = [ln for ln in (answer or "").splitlines() if " | " not in ln]
    return "\n".join(lines).strip()


# ── deep_agent / doc_qa 的状态 note ─────────────────────────────


def _trace_note(data: Optional[Dict[str, Any]]) -> str:
    """deep_agent 思维链摘要（工具 → 结果条数；完整交互式折叠留 P3）。"""
    if not isinstance(data, dict):
        return ""
    trace = data.get("trace")
    if not isinstance(trace, dict):
        return ""
    plan = trace.get("plan") or []
    steps = trace.get("steps")
    if not isinstance(steps, list):
        steps = []
    if not steps and not plan:
        return ""

    status_mark = {"ok": "✓", "empty": "∅", "error": "✗"}
    lines = ["🧠 思维链"]
    for s in steps[:_TRACE_STEPS_SHOWN]:
        if not isinstance(s, dict):
            continue
        tool = s.get("tool") or "?"
        query = _clip(str(s.get("query") or ""), 24)
        mark = status_mark.get(str(s.get("status") or "ok"), "·")
        line = f"{mark} {tool}"
        if query:
            line += f"「{query}」"
        count = s.get("records_count")
        if isinstance(count, (int, float)) and count > 0:
            line += f" · {int(count)} 条"
        lines.append(line)
    if len(steps) > _TRACE_STEPS_SHOWN:
        lines.append("…")

    summary = f"规划 {len(plan)} 步 / 执行 {len(steps)} 步"
    if trace.get("rounds"):
        summary += f" / {trace['rounds']} 轮"
    reflection = trace.get("reflection")
    if isinstance(reflection, dict) and reflection.get("verdict"):
        summary += f" / 反思 {reflection['verdict']}"
    lines.append(summary)
    return "\n".join(lines)


def _doc_qa_note(data: Optional[Dict[str, Any]]) -> str:
    if not isinstance(data, dict):
        return ""
    status = data.get("status")
    if status == "success":
        sources = data.get("sources")
        count = len(sources) if isinstance(sources, list) else 0
        mode = data.get("mode") or ""
        text = f"📄 已回答 · {count} 个来源"
        if mode:
            text += f" · 模式 {mode}"
        return text
    if status == "no_doc_attached":
        return "📄 未检测到已上传的文档，可先上传文件后再提问"
    if status == "failed":
        return "📄 文档处理失败，请重新上传后再试"
    return ""


# ── 出口 ────────────────────────────────────────────────────────


def render_text_reply(resp: Dict[str, Any]) -> str:
    """错误码/降级场景的纯文本消息内容（成功走 render_card_reply）。"""
    error_code = resp.get("error_code")
    if error_code:
        return _error_message(error_code, resp.get("answer") or "")

    parts: List[str] = []
    answer = _clip(resp.get("answer") or "", _MAX_ANSWER_CHARS)
    if answer:
        parts.append(answer)

    reply_type = resp.get("reply_type")
    if reply_type == "kb_answer":
        block = _citations_block(resp.get("citations") or [])
        if block:
            parts.append(block)
    elif reply_type == "clarify":
        candidates = (resp.get("data") or {}).get("candidates") or []
        if candidates:
            parts.append("请选择一项（回复序号或直接输入）：")
            parts.extend(f"{i}. {c}" for i, c in enumerate(candidates, 1))

    return "\n\n".join(parts).strip() or "（没有可显示的内容）"


def render_card_reply(resp: Dict[str, Any]) -> Dict[str, Any]:
    """ChatResponse dict → 飞书卡片 JSON 1.0（消息 API 最通用的 interactive 格式）。"""
    error_code = resp.get("error_code")
    if error_code:
        return _error_card(error_code, resp.get("answer") or "")

    reply_type = resp.get("reply_type")
    answer = _clip(resp.get("answer") or "", _MAX_ANSWER_CHARS)
    elements: List[Dict[str, Any]] = []

    if answer:
        elements.append({"tag": "markdown", "content": answer})

    if reply_type == "price_records":
        data = resp.get("data")
        records = data.get("records") if isinstance(data, dict) else None
        if isinstance(records, list) and len(records) > 1:
            table, truncated = _table_element(data)
            if table is not None:
                body = _strip_table_lines(answer)
                # 列表行已由表格承载：正文只留引导句/结语，避免同批数据出现两次
                elements = [{"tag": "markdown", "content": body}] if body else []
                elements.append(table)
                if truncated:
                    elements.append(
                        _note_element(
                            f"仅显示前 {_MAX_TABLE_ROWS} 条，完整结果请回电脑端查看"
                        )
                    )
    elif reply_type == "kb_answer":
        citations = resp.get("citations") or []
        if citations:
            if _CITATIONS_COLLAPSIBLE:
                # 折叠面板：默认收起，标题带来源数；展开看逐条引用（客户端 ≥7.9）
                elements.append(
                    _collapsible_panel(
                        f"📚 引用来源（{len(citations)} 条）",
                        [{"tag": "markdown", "content": "\n".join(_citations_lines(citations))}],
                    )
                )
            else:
                block = _citations_block(citations)
                if block:
                    elements.append({"tag": "hr"})
                    elements.append(
                        {"tag": "div", "text": {"tag": "lark_md", "content": block}}
                    )
    elif reply_type == "deep_agent":
        note = _trace_note(resp.get("data"))
        if note:
            elements.append(_note_element(note))
    elif reply_type == "doc_qa":
        note = _doc_qa_note(resp.get("data"))
        if note:
            elements.append(_note_element(note))
    elif reply_type == "clarify":
        candidates = (resp.get("data") or {}).get("candidates") or []
        if candidates:
            lines = ["请选择一项（回复序号或直接输入）："]
            lines.extend(f"{i}. {c}" for i, c in enumerate(candidates, 1))
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
            )

    latency_ms = resp.get("latency_ms")
    if latency_ms:
        elements.append(_note_element(f"⏱ {float(latency_ms) / 1000:.1f}s"))

    if not elements:
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": "（没有可显示的内容）"}}
        )

    card: Dict[str, Any] = {"config": {"wide_screen_mode": True}}
    if reply_type != "kb_answer":
        # kb_answer 顶格渲染（用户反馈 09-25）：header 把回答往下推、抢注意力
        card["header"] = _header_for(reply_type)
    card["elements"] = elements
    return card


def _error_card(error_code: str, answer: str) -> Dict[str, Any]:
    """错误卡：提示 header + 友好文案（未知码带 code，便于排查）。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": _header_for("fallback"),
        "elements": [{"tag": "markdown", "content": _error_message(error_code, answer)}],
    }


__all__ = ["render_text_reply", "render_card_reply"]
