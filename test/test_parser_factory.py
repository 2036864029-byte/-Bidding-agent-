"""L6 — parser_factory 接线测试（后端热切换 + 三档路由开关）。

默认后端必须是 local：本地开发行为零变化，是这次改动的硬约束。
"""

from __future__ import annotations

from core.config.settings import Settings
from public_kb.ingestion.normalizers.pdf.pdf_router import PdfRouter
from public_kb.ingestion.parser_factory import build_pdf_parser, resolve_backend
from public_kb.services.mineru_api_parser import MinerUApiParser
from public_kb.services.mineru_cloud_parser import MinerUCloudParser
from public_kb.services.mineru_parser import MinerUParser


def _settings(tmp_path, *, backend: str, tiered: bool = False) -> Settings:
    """构造隔离的 Settings：输出目录指向 tmp，避免污染仓库 DATA/。"""
    s = Settings()
    s.mineru_output_dir = str(tmp_path)
    s.pdf_parse_backend = backend
    s.pdf_tiered_routing_enabled = tiered
    return s


def test_default_switch_returns_mineru_parser(tmp_path):
    """默认 backend=local → MinerUParser（M1 行为不变）。"""
    s = _settings(tmp_path, backend="local")
    assert resolve_backend(s) == "local"
    p = build_pdf_parser(s)
    assert isinstance(p, MinerUParser)
    assert not isinstance(p, PdfRouter)


def test_settings_default_backend_is_local(tmp_path):
    """未显式设置时，Settings 默认值必须是 local（不是 official）。"""
    s = Settings()
    s.mineru_output_dir = str(tmp_path)
    assert s.pdf_parse_backend == "local"


def test_official_backend_returns_cloud_parser(tmp_path):
    """backend=official → MinerUCloudParser（云服务器用官方 API）。"""
    s = _settings(tmp_path, backend="official")
    p = build_pdf_parser(s)
    assert isinstance(p, MinerUCloudParser)
    assert not isinstance(p, MinerUParser)


def test_selfhosted_backend_returns_api_parser(tmp_path):
    """backend=selfhosted → MinerUApiParser（自建 /parse 服务，既有协议）。"""
    s = _settings(tmp_path, backend="selfhosted")
    p = build_pdf_parser(s)
    assert isinstance(p, MinerUApiParser)


def test_unknown_backend_falls_back_to_local(tmp_path):
    """非法取值回退 local，绝不静默切到付费云 API。"""
    s = _settings(tmp_path, backend="cloudflare")
    assert resolve_backend(s) == "local"
    assert isinstance(build_pdf_parser(s), MinerUParser)


def test_empty_backend_falls_back_to_local(tmp_path):
    """空值同样回退 local（.env 里写了键没写值的情况）。"""
    s = _settings(tmp_path, backend="")
    assert resolve_backend(s) == "local"
    assert isinstance(build_pdf_parser(s), MinerUParser)


def test_backend_value_is_whitespace_and_case_insensitive(tmp_path):
    """` OFFICIAL ` 这类写法应被规范化，而不是因大小写回落 local。"""
    s = _settings(tmp_path, backend="  OFFICIAL  ")
    assert resolve_backend(s) == "official"
    assert isinstance(build_pdf_parser(s), MinerUCloudParser)


def test_tiered_switch_returns_pdf_router(tmp_path):
    """pdf_tiered_routing_enabled=true → PdfRouter（不论 backend）。"""
    s = _settings(tmp_path, backend="local", tiered=True)
    p = build_pdf_parser(s)
    assert isinstance(p, PdfRouter)
    assert not isinstance(p, MinerUParser)


def test_tiered_routing_uses_selected_backend_for_tier_c(tmp_path):
    """三档路由开启时，Tier C 解析器要跟着 backend 走（不是写死自建）。"""
    s = _settings(tmp_path, backend="official", tiered=True)
    router = build_pdf_parser(s)
    assert isinstance(router, PdfRouter)
    assert isinstance(router._miner_u_router.parser, MinerUCloudParser)


def test_tiered_routing_local_keeps_selfhosted_tier_c(tmp_path):
    """backend=local + 三档路由 → Tier C 仍是原有自建服务（行为不变）。"""
    s = _settings(tmp_path, backend="local", tiered=True)
    router = build_pdf_parser(s)
    assert isinstance(router, PdfRouter)
    assert isinstance(router._miner_u_router.parser, MinerUApiParser)


def test_all_parsers_expose_parse_interface(tmp_path):
    """duck-typed：所有后端都有 .parse(pdf_path) -> str，PdfSource 不挑食。"""
    for backend in ("local", "official", "selfhosted"):
        parser = build_pdf_parser(_settings(tmp_path, backend=backend))
        assert callable(getattr(parser, "parse", None)), backend
