"""图片 OCR normalizer 离线测试（W4-3）— 注入假 engine，不触真实 OCR/网络。"""

from __future__ import annotations

from types import SimpleNamespace

from public_kb.ingestion.ir import NormalizedDocument


def _source(content="", path="/tmp/x.png") -> NormalizedDocument:
    return NormalizedDocument(
        doc_id="i1", source_format="image", ir_format="markdown",
        content=content, source_path=path,
        metadata={"source_hash": "h"},
    )


class _FakeEngine:
    """注入的假 OCR：返回预置识别行。"""

    def __init__(self, rows):
        self._rows = rows

    def __call__(self, path):
        return self._rows, [0.1, 0.01, 0.01]


def _row(text, top, bottom, x0=0):
    return ([[x0, top], [x0 + 100, top], [x0 + 100, bottom], [x0, bottom]], text, 0.95)


def test_image_ocr_normalize_basic():
    from public_kb.ingestion.normalizers.image import ImageOcrNormalizer

    engine = _FakeEngine([
        _row("招标方式有哪几种？", top=10, bottom=30),
        _row("分为公开招标和邀请招标", top=50, bottom=70),
    ])
    n = ImageOcrNormalizer(engine=engine)
    out = n.normalize(_source())
    assert len(out) == 1
    doc = out[0]
    assert doc.ir_format == "markdown"
    assert "招标方式有哪几种？" in doc.content
    assert "分为公开招标和邀请招标" in doc.content
    assert doc.metadata["quality_score"] == 1.0
    assert doc.metadata["parser_version"] == "rapidocr/v1"


def test_image_ocr_orders_rows_and_inserts_paragraph_gap():
    """按 y 排序 + 大段距插空行。"""
    from public_kb.ingestion.normalizers.image import ImageOcrNormalizer

    # 乱序输入：先给下面的行，再给上面的行 → 输出按 y 重排；
    # 段内行距 4px（≤14 阈值）不分段，第三段隔 140px → 空行分隔
    engine = _FakeEngine([
        _row("第二行", top=34, bottom=54),
        _row("第一行", top=10, bottom=30),
        _row("第三段", top=200, bottom=220),
    ])
    doc = ImageOcrNormalizer(engine=engine).normalize(_source())[0]
    lines = [ln for ln in doc.content.splitlines()]
    assert lines[0] == "第一行" and lines[1] == "第二行"
    assert "" in lines            # 段距断段插入空行
    assert "第三段" in lines


def test_image_ocr_degraded_when_engine_missing():
    """引擎不可用 → 降级产出（quality<0.8），不抛错、不产出乱码。"""
    from public_kb.ingestion.normalizers.image import ImageOcrNormalizer

    n = ImageOcrNormalizer(engine=None)
    # 不触发进程单例（那会尝试加载真实模型）→ 直接测降级分支：注入一个"加载失败"的
    # 引擎对象即可模拟。这里用会抛异常的假 engine 走 ocr_error 分支。
    engine = SimpleNamespace(__call__=lambda path: (_ for _ in ()).throw(RuntimeError("boom")))
    out = ImageOcrNormalizer(engine=engine).normalize(_source())
    assert out[0].metadata["quality_score"] < 0.8
    assert out[0].metadata["ocr_note"]


def test_image_ocr_empty_result_degraded():
    from public_kb.ingestion.normalizers.image import ImageOcrNormalizer

    engine = _FakeEngine([])
    doc = ImageOcrNormalizer(engine=engine).normalize(_source())[0]
    assert doc.metadata["quality_score"] == 0.5
    assert "no_text_found" in doc.metadata["ocr_note"]


def test_build_normalizer_and_router_classify():
    from public_kb.ingestion.normalizers import build_normalizer
    from public_kb.ingestion.router import FormatRouter

    n = build_normalizer("image")
    assert type(n).__name__ == "ImageOcrNormalizer"

    r = FormatRouter()
    assert r.classify("a.png") == "image"
    assert r.classify("a.jpeg") == "image"
    assert r.classify("a.tif") == "image"
    # 嗅探兜底：无扩展名但 PNG magic
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix="", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\nrest")
        p = Path(f.name)
    try:
        assert r.classify(p) == "image"
    finally:
        p.unlink()
