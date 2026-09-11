"""core.observability.tracing — OTel → Langfuse 追踪（E0-3）。

设计要点
--------
1. **未配置即 no-op**：`ENABLE_TRACING=false` 或密钥缺失时 `get_tracer()` 返回 OTel
   的 NoOpTracer，`span()` 零开销、不触网、不改变行为——dev/test 与 970 条测试不受影响。
2. **走 OTel 标准而非 Langfuse 私有 API**：OTLP/HTTP 导出到 Langfuse 的
   `/api/public/otel/v1/traces`，属性遵循 GenAI 语义约定（`gen_ai.*`）。
   换后端（Phoenix / Jaeger / 阿里云 ARMS）只改 endpoint，业务代码零改动。
3. **跨线程**：span 父子关系依赖 contextvars。本项目 SSE 路径在 daemon 线程跑图
   （W3-2 已实测 `threading.Thread` 不自动拷贝 contextvars），端点在启动线程前
   显式 `copy_context().run(...)`，故父子 span 仍能正确嵌套——E0-3 需专项验证。

用法::

    from core.observability.tracing import setup_tracing, span

    setup_tracing()                       # 进程启动一次（API main / CLI）
    with span("node.router", **{"ztb.node": "router"}) as sp:
        sp.set_attribute("ztb.intent", intent)
"""

from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_ENABLED = False
_PROVIDER: Optional[Any] = None


def is_tracing_enabled() -> bool:
    """追踪是否已启用（供测试与诊断）。"""
    return _ENABLED


def setup_tracing(settings: Optional[Any] = None) -> bool:
    """配置 OTel TracerProvider → Langfuse OTLP。返回是否启用成功。

    幂等：重复调用直接返回当前状态。任何异常都降级为"保持关闭"，不影响主流程。
    """
    global _ENABLED, _PROVIDER
    if _ENABLED:
        return True

    try:
        from core.config.settings import Settings

        s = settings or Settings()
    except Exception as e:  # 配置读取失败不阻断
        logger.warning("tracing: 读取配置失败，保持关闭: %s", e)
        return False

    if not getattr(s, "enable_tracing", False):
        logger.info("tracing: ENABLE_TRACING=false，保持关闭")
        return False
    public = getattr(s, "langfuse_public_key", "")
    secret = getattr(s, "langfuse_secret_key", "")
    if not public or not secret:
        logger.warning("tracing: 缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY，保持关闭")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        host = str(getattr(s, "langfuse_host", "http://localhost:3000")).rstrip("/")
        auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{host}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": str(getattr(s, "tracing_service_name", "ztb-agent"))}
            )
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _PROVIDER = provider
        _ENABLED = True
        logger.info("tracing: 已启用 → %s（OTLP）", host)
        return True
    except Exception as e:
        logger.warning("tracing: 初始化失败，保持关闭: %s", e)
        return False


def get_tracer(name: str = "ztb"):
    """返回 tracer；未启用时为 NoOpTracer（OTel 默认行为，零开销）。"""
    from opentelemetry import trace

    return trace.get_tracer(name)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """开启一个 span（未启用时为 no-op）。异常自动 record_exception 并向上抛。"""
    from opentelemetry.trace import Status, StatusCode

    tracer = get_tracer()
    with tracer.start_as_current_span(name) as sp:
        for key, value in attrs.items():
            if value is not None:
                sp.set_attribute(key, value)
        try:
            yield sp
        except Exception as e:
            sp.record_exception(e)
            sp.set_status(Status(StatusCode.ERROR, str(e)[:200]))
            raise


def set_span_attrs(sp: Optional[Any], **attrs: Any) -> None:
    """给 span 批量写属性（sp 为 None 时静默跳过）。"""
    if sp is None:
        return
    for key, value in attrs.items():
        if value is not None:
            sp.set_attribute(key, value)


#: observation input/output 截断上限（防超长问题/答案撑爆 span）
OBSERVATION_IO_LIMIT = 2000


def observation_io(
    input_text: Optional[str], output_text: Optional[str], *,
    limit: int = OBSERVATION_IO_LIMIT,
) -> dict:
    """Langfuse observation 的 input/output 属性（E4-1）。

    Langfuse v4（OTLP）读取 `langfuse.observation.input` / `.output` 作为观测的
    输入/输出；节点 span 借此带入问题与答案，使 trace 可被采样回灌金标。
    """
    attrs: dict = {}
    if input_text:
        attrs["langfuse.observation.input"] = str(input_text)[:limit]
    if output_text:
        attrs["langfuse.observation.output"] = str(output_text)[:limit]
    return attrs


def shutdown_tracing() -> None:
    """冲刷并关闭 provider（进程退出前调用，保证 span 送达）。"""
    global _ENABLED, _PROVIDER
    if _PROVIDER is not None:
        try:
            _PROVIDER.shutdown()
        except Exception as e:  # 关闭失败不阻断退出
            logger.warning("tracing: 关闭失败: %s", e)
    _PROVIDER = None
    _ENABLED = False


__all__ = [
    "is_tracing_enabled",
    "setup_tracing",
    "get_tracer",
    "span",
    "set_span_attrs",
    "observation_io",
    "shutdown_tracing",
]
