# 功能：LLM Provider 抽象基类。
"""LLMProvider 抽象 — 收敛"按槽位配置构造模型"这一差异点（Phase 0 / A-3）。

设计说明（忠于现状，滚动式收紧）：
  P0 阶段节点/链路的运行时对象仍为 LangChain ``BaseChatModel``（Runnable）——
  router 依赖 ``.with_structured_output`` / ``.bind_tools``、generation 链依赖
  ``|`` 拼接。若抽象成非 Runnable 包装类并接入调用点，会造成行为漂移，违背
  P0"不改任何行为"。因此本 ABC 先只声明 *构造* 差异：Provider.build() 按槽位
  （model / temperature 覆盖）返回可用的 ``BaseChatModel``。

  chat / structured_output / stream 的门面抽象推迟到 P6 工具化阶段：届时引入
  非 Runnable 的 Provider 门面供 tool / 多 Agent 复用（原 A-3 草案把 router 的
  ``with_structured_output`` 误归因到 ABC，实为对 Runnable 的能力依赖）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from langchain_core.language_models import BaseChatModel


class LLMProvider(ABC):
    """按槽位配置构造一个 LangChain Chat 模型的 Provider。

    子类以类属性 ``provider_name`` 声明注册名（registry 以名取 Provider）。
    """

    #: 注册名（models.yaml 中 slot.provider 引用）
    provider_name: str = ""

    def __init__(self, settings) -> None:
        self._settings = settings

    @abstractmethod
    def build(
        self,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> BaseChatModel:
        """构造模型实例。

        Args:
            model: 显式模型名；None 时沿用 settings.llm_model（支持 env 覆盖）。
            temperature: 显式温度；None 时沿用 settings.llm_temperature。
        """
        raise NotImplementedError
