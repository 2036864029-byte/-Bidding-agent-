# 功能：清理 MinerU Markdown 噪声、公式和页眉页脚等干扰内容。
"""
文本清洗器 — 对 MinerU 产出的 Markdown 进行后处理去噪。

清洗规则（按序执行）：
1. 去除页眉页脚 —— 连续出现的重复短行（如每页顶部的书名/章节名）
2. 去除页码 —— 独立的纯数字行
3. 去除噪声短行 —— 仅删除无汉字的符号/装饰短行（A2-1：不再"<10 字全删"，
   含汉字的正文碎片如"理机构。"必须保留，否则双栏行尾丢字）
4. 压缩多余空行 —— 连续 3+ 空行压缩为 2 个
"""

from __future__ import annotations

import re
from collections import Counter

# 含汉字（CJK 统一表意文字块）：短行含汉字 = 正文碎片/短标题，保留
_CJK_RE = re.compile(r"[一-鿿]")


class TextCleaner:
    """Markdown 文本清洗器，输入原始 MD 文本，输出去噪后的干净文本。"""

    # 匹配独立的纯数字行（页码）
    _PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

    # 页眉/页脚去重门槛：行太长或有 CJK 句末符 = 内容，不因重复而被当页眉删
    _HEADER_MAX_LEN = 40
    _SENTENCE_ENDERS = "。！？…；"

    # A2-3：页标记 / 图片链接行（装配层写入的注释标签与 MinerU 图片引用，
    # 属于过程产物，不得进入干净文本资产）
    _PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:\s*\d+\s*-->\s*$")
    _IMAGE_LINK_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")

    # A2-2：LaTeX/乱码清洗
    # 常见转义（可重复）：\times→×、\%→%、\@→@、\#→#、U+00A0→空格
    _LATEX_ESCAPES = (
        (re.compile(r"\\times(?![A-Za-z])"), "×"),
        (re.compile(r"\\textcircled\{([^}]*)\}"), r"(\1)"),  # 圈码 → (N)
        (re.compile(r"\\%"), "%"),
        (re.compile(r"\\@"), "@"),
        (re.compile(r"\\#"), "#"),
        (re.compile(r" "), " "),
    )
    # 剥命令包装保留内层：\boldsymbol{...}/\mathit{...}/\mathrm{X}/\textbf{...}
    # 等。容忍命令与 { 之间的空格、以及一层嵌套花括号（OCR 输出如
    # `\boldsymbol { { \mathit { 1 1 } } }`），循环替换至稳定。
    _LATEX_CMD_BRACE_RE = re.compile(
        r"\\(?:boldsymbol|mathit|mathrm|textbf|textit|textsf|mathtt|mathcal)"
        r"\s*\{((?:[^{}]|\{[^{}]*\})*)\}"
    )
    # $...$ 伪数学剥壳（保留内层：$@$→@、$10\%$→10%）
    _DOLLAR_MATH_RE = re.compile(r"\$([^$]*)\$")
    # 百分号丢失：数字后 ??（评估实测 `2??` → `2%`，PyMuPDF 字体映射失败）
    _PERCENT_LOST_RE = re.compile(r"(\d)\?\?")

    # 法律文本标题（任务 M4）：短行白名单 + 页眉去重豁免
    # 匹配 "第一章" "第X节" "第X条" "第X款" "第X项" 及其带 # 前缀的标题行。
    # 这些行即使很短、或反复出现（如页眉/页脚），也不得被清洗规则丢弃，
    # 否则会破坏章节结构（正文与页眉同文时连真实标题也会被误删）。
    _LEGAL_HEADING_RE = re.compile(
        r"^#{0,6}\s*第[一二三四五六七八九十百千\d]+[章节条款项部分编]"
    )

    @staticmethod
    def clean(raw_markdown: str) -> str:
        """对原始 Markdown 文本执行全链路清洗。

        Args:
            raw_markdown: MinerU 解析产出的原始 Markdown 字符串。

        Returns:
            清洗后的干净 Markdown 字符串。
        """
        lines: list[str] = raw_markdown.split("\n")

        # ── 步骤 1: 检测并移除重复出现的页眉行 ──
        lines = TextCleaner._remove_repeating_headers(lines)

        # ── 步骤 1.5: 剥离页标记与图片链接行（A2-3）──
        lines = [
            line for line in lines
            if not TextCleaner._PAGE_MARKER_RE.match(line)
            and not TextCleaner._IMAGE_LINK_RE.match(line)
        ]

        # ── 步骤 2: 移除页码行 ──
        lines = [
            line for line in lines
            if not TextCleaner._PAGE_NUMBER_RE.match(line)
        ]

        # ── 步骤 3: 移除噪声短行（A2-1：不再"<10 字全删"）──
        # 含汉字的短行是正文碎片（双栏行尾，如"理机构。"）或短标题，必须保留；
        # 旧规则按长度一刀切把它们当噪声丢弃，是跨页/跨栏丢字的根因之一。
        # 仅删除无汉字的纯符号/装饰短行（页标记、点线、隔离线等）。
        lines = [
            line for line in lines
            if TextCleaner._keep_line(line)
        ]

        # ── 步骤 3.5: 行级 LaTeX/乱码清洗（A2-2）──
        lines = [
            TextCleaner._clean_latex(line) if line.strip() else line
            for line in lines
        ]

        # ── 步骤 4: 压缩连续空行 ──
        cleaned: list[str] = []
        blank_count = 0
        for line in lines:
            if line.strip() == "":
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)
            else:
                blank_count = 0
                cleaned.append(line)

        return "\n".join(cleaned)

    @staticmethod
    def _keep_line(line: str) -> bool:
        """A2-1 短行保留判定：仅"无汉字的符号短行"视为噪声删除。

        保留：空行 / Markdown 标题 / 分隔线 / 表格行（| 前缀）/ 法律标题 /
        含汉字的短行（正文碎片）。删除：不含汉字的纯符号/装饰短行。
        """
        s = line.strip()
        if not s or s.startswith("#") or s in ("---", "***", "___") or s.startswith("|"):
            return True
        if TextCleaner._LEGAL_HEADING_RE.match(s):
            return True
        if len(s) < 10:
            return bool(_CJK_RE.search(s))
        return True

    @staticmethod
    def _clean_inner_latex(s: str) -> str:
        """LaTeX 命令包装剥壳（容忍空格与一层嵌套），循环至稳定。"""
        prev = None
        while prev != s:
            prev = s
            s = TextCleaner._LATEX_CMD_BRACE_RE.sub(r"\1", s)
        for pat, repl in TextCleaner._LATEX_ESCAPES:
            s = pat.sub(repl, s)
        return s.strip()

    @staticmethod
    def _clean_latex(text: str) -> str:
        """A2-2：行级 LaTeX/乱码清洗。

        - 常见转义：``\\times``→×、``\\%``→%、``\\@``→@、``\\#``→#、U+00A0→空格；
        - 剥 ``\\boldsymbol/\\mathit/\\mathrm/\\textbf`` 等命令包装保留内层；
        - ``$...$`` 伪数学剥壳保留内层（``$@$``→@、``$10\\%$``→10%）；
        - 数字后 ``??``（PyMuPDF 字体映射丢失的百分号）→ ``%``。
        """
        for pat, repl in TextCleaner._LATEX_ESCAPES:
            text = pat.sub(repl, text)
        text = TextCleaner._clean_inner_latex(text)
        text = TextCleaner._DOLLAR_MATH_RE.sub(
            lambda m: TextCleaner._clean_inner_latex(m.group(1)), text
        )
        text = TextCleaner._PERCENT_LOST_RE.sub(r"\1%", text)
        return text

    @staticmethod
    def _remove_repeating_headers(lines: list[str]) -> list[str]:
        """识别并移除在所有页面上重复出现的行（页眉/页脚）。

        算法：统计每一行的出现频次，若某行在文档中出现 >= 5 次
        且长度 < 80 字符，则认为它是页眉/页脚行，全部移除。
        任务 M4 豁免：合法法律文本标题（第X章/第X条等）即使反复出现
        （如章节标题页眉"第一章 总则"），也不被当作页眉删除，
        否则会破坏章节结构。
        内容行豁免（2026-09-05）：行内含 CJK 句末符（。！？…；）或长于
        40 字符的行即使整行重复 >=5 次也不删——问答书里不同题号常共用同一
        长答案，重排粘行后可能逐字节相同，误删会把正文整段吃掉（页眉/页脚
        均为短且不含句末符的裸行）。
        """
        stripped = [line.strip() for line in lines]
        counter = Counter(
            s for s in stripped if s and len(s) < 80
        )
        repeated = {
            s for s, count in counter.items()
            if count >= 5
            and not TextCleaner._LEGAL_HEADING_RE.match(s)
            and len(s) <= TextCleaner._HEADER_MAX_LEN
            and not any(ch in s for ch in TextCleaner._SENTENCE_ENDERS)
        }

        if not repeated:
            return lines

        return [
            line for line in lines
            if line.strip() not in repeated
        ]
