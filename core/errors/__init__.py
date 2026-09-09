"""core.errors — 统一错误与容错基础设施。"""

from .fallback import accepts_config, with_fallback

__all__ = ["accepts_config", "with_fallback"]
