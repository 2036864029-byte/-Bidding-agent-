"""M4 清洗规则保护条款号的单元测试。"""

from __future__ import annotations

from public_kb.ingestion.transforms.cleaner import TextCleaner

# ── 短行白名单：第X条/第X章 不被删 ────────────────────────

# 正文需 >10 字符，避免被既有"短行(<10)删除"规则正常清理，混淆断言目标
_BODY = "本条正文内容足够长，确保可以通过既有清洗规则。"


def test_legal_heading_short_line_is_kept():
    raw = f"第一条\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned
    assert _BODY in cleaned


def test_legal_heading_with_md_prefix_is_kept():
    raw = f"## 第一条\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned


def test_legal_chapter_heading_is_kept():
    raw = f"第一章 总则\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一章 总则" in cleaned


def test_noise_short_line_is_still_removed():
    # 无汉字的纯符号短行仍应被删除（A2-1 噪声黑名单：仅符号装饰行）
    raw = f"........\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "........" not in cleaned
    assert _BODY in cleaned


def test_cjk_fragment_short_line_is_kept():
    # A2-1 核心：含汉字的短行是正文碎片（双栏行尾，如"理机构。"），必须保留
    raw = f"理机构。\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "理机构。" in cleaned
    assert _BODY in cleaned


def test_page_marker_line_is_stripped():
    # A2-3：`<!-- page: N -->` 是装配层过程产物，不得进入干净文本
    raw = f"<!-- page: 16 -->\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "<!-- page" not in cleaned
    assert _BODY in cleaned


def test_image_link_line_is_stripped():
    # A2-3：MinerU 图片引用 `![](images/...)` 从干净文本剥离
    raw = f"![](images/001.jpg)\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "images/001.jpg" not in cleaned
    assert _BODY in cleaned


# ── A2-2 LaTeX/乱码清洗 ───────────────────────────────────

def test_latex_circled_number_normalized():
    raw = f"条款见 \\textcircled{{1}} 标注的内容足够长。\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "(1)" in cleaned
    assert "\\textcircled" not in cleaned


def test_latex_command_wrapper_stripped():
    raw = f"\\boldsymbol{{ABC}} 内容足够长确保保留。\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "ABC" in cleaned
    assert "\\boldsymbol" not in cleaned


def test_latex_nested_wrapper_with_spaces_stripped():
    # OCR 输出带空格与嵌套：\boldsymbol { { \mathit { 1 1 } } }
    raw = "\\boldsymbol { { \\mathit { 1 1 } } } 内容足够长确保保留。\n" + _BODY + "\n"
    cleaned = TextCleaner.clean(raw)
    assert "\\boldsymbol" not in cleaned
    assert "\\mathit" not in cleaned
    assert "1 1" in cleaned


def test_dollar_math_shell_kept_inner():
    raw = f"邮箱为 $@$ 163 的地址内容足够长。\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "@" in cleaned
    assert "$" not in cleaned


def test_percent_lost_question_marks_recovered():
    raw = f"保证金不得超过 2?? 的比例内容足够长。\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "2%" in cleaned
    assert "2??" not in cleaned


def test_plain_number_line_is_still_removed():
    # 纯页码行仍被删除（原行为不变）
    raw = f"12\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "12" not in cleaned


def test_page_number_after_heading_is_still_removed():
    raw = f"## 第一条\n12\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned
    assert "12" not in str(cleaned.split("第一条")[1])  # 页码行被删，正文保留


# ── 页眉去重豁免：章节标题页眉不被删 ──────────────────────

def test_repeating_legal_heading_header_is_preserved():
    # 章节标题作为页眉反复出现（>=5 次）→ 不因"重复行"被删除
    header = "第一编 总则"
    raw = "\n".join([header] * 6 + [_BODY])
    cleaned = TextCleaner.clean(raw)
    assert header in cleaned  # 章节标题被保留


def test_repeating_noise_header_is_still_removed():
    # 非法律标题的普通页眉反复出现（>=5 次）→ 仍被删除（原行为不变）
    header = "机械工业出版社"
    raw = "\n".join([header] * 6 + [_BODY])
    cleaned = TextCleaner.clean(raw)
    assert header not in cleaned
    assert _BODY in cleaned
