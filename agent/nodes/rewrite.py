"""多轮指代消解 / 查询改写节点（W3-1）。

背景（审计 D）：`messages` 按 thread_id 持久（有记忆），但所有业务节点只读
`messages[-1]`（无理解）——"那它呢？""换成 A 公司呢？"这类追问拿不到指代对象。

做法：
  - 本节点挂在**图入口**（single 与 supervisor 都从它开始），在路由/业务之前运行。
  - **确定性预判**（`looks_like_followup`）：仅在疑似追问时花一次轻量 LLM 调用
    （自包含问题零成本直通，不调模型）。
  - 改写结果**原位替换**最后一条用户消息（同 message id，add_messages 按 id 合并），
    因此 router 与所有业务节点看到的都是归一化后的问题。
  - **失败软降级**：任何异常（LLM 超时等）→ 原样返回，不阻断本轮。

已知限制：预判只认指代线索词（那/这/它/换/继续/…），纯省略式追问（"有没有不良
记录？"）暂不命中 —— 不引入误判，遗留问题由后续按 golden 数据扩展预判。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from core.config.settings import Settings
from core.llm.registry import create_llm_for_slot

logger = logging.getLogger(__name__)

MAX_TURNS = 4

#: 疑似追问的指代/省略线索（命中其一才花 LLM 调用；宁缺毋滥）
_REFERENTIAL_HINTS = (
    "那", "这", "它", "该", "其", "换", "继续", "上述",
    "它们", "他们", "他的", "她的", "其他", "另外", "还有", "同上",
)

_REWRITE_SYSTEM = (
    "你是招投标助手的「问题归一化器」。给定对话历史和当前用户问题：\n"
    "1. 若当前问题是自包含的（单独即可检索/回答），原样输出，不做任何改动。\n"
    "2. 若当前问题是追问（含「那/它/这/该/换成/继续」等指代，或省略了上文出现过的"
    "公司/项目/编号等实体），改写为去掉指代、补齐实体的独立问题。\n"
    "3. 不得臆造历史中不存在的实体或信息，不得改变问句的查询意图。\n"
    "只输出改写后的问题文本，不要任何解释、引号或标点修饰。"
)

_REWRITE_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _REWRITE_SYSTEM),
    ("human", "【对话历史】\n{history}\n\n【当前问题】\n{question}\n\n请只输出归一化后的问题："),
])


def _history_text(messages: list, max_turns: int) -> str:
    """把最近 N 轮历史格式化为文本（用户/助手交替）。"""
    recent = messages[-(max_turns * 2):]
    lines = []
    for message in recent:
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        lines.append(f"{role}: {str(message.content)[:200]}")
    return "\n".join(lines)


def looks_like_followup(messages) -> bool:
    """确定性预判：当前问题是否疑似追问（决定是否调用改写 LLM）。"""
    users = [m for m in messages if isinstance(m, HumanMessage)]
    if len(users) < 2:
        return False  # 首问必自包含
    if not messages or not isinstance(messages[-1], HumanMessage):
        return False
    last = str(messages[-1].content)
    return any(hint in last for hint in _REFERENTIAL_HINTS)


def rewrite_question(question: str, history_text: str, llm: BaseChatModel) -> str:
    """调用 LLM 归一化问题；失败/空结果时原样返回。"""
    try:
        chain = _REWRITE_TEMPLATE | llm | StrOutputParser()
        out = chain.invoke({"history": history_text, "question": question}).strip()
        if out:
            logger.info("查询改写: %r -> %r", question[:60], out[:120])
            return out
    except Exception as exc:
        logger.warning("查询改写 LLM 调用失败，沿用原问题: %s", exc)
    return question


def build_rewrite_node(
    *,
    llm: Optional[BaseChatModel] = None,
    llm_factory: Optional[Callable[[], BaseChatModel]] = None,
    max_turns: int = MAX_TURNS,
) -> Callable[[dict, Optional[RunnableConfig]], dict]:
    """构建查询改写节点 `(state, config?) -> dict`。

    Args:
        llm / llm_factory: 注入（测试用）；缺省用 models.yaml 的 rewrite 槽位。
        max_turns: 送入改写器的最近轮数。
    """
    _llm: Optional[BaseChatModel] = llm

    def _get_llm() -> BaseChatModel:
        nonlocal _llm
        if _llm is None:
            _llm = (
                llm_factory() if llm_factory is not None
                else create_llm_for_slot("rewrite", Settings())
            )
        return _llm

    def node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        messages = state.get("messages") or []
        try:
            if not looks_like_followup(messages):
                return {}
            question = str(messages[-1].content)
            rewritten = rewrite_question(question, _history_text(messages, max_turns), _get_llm())
            if rewritten and rewritten != question:
                last = messages[-1]
                # 同 id 替换：add_messages 按 id 合并，router/业务节点看到归一化问题
                return {"messages": [HumanMessage(content=rewritten, id=last.id)]}
        except Exception as exc:  # 软降级：不因改写失败阻断本轮
            logger.warning("查询改写节点异常，沿用原问题: %s", exc)
        return {}

    return node


__all__ = [
    "MAX_TURNS",
    "build_rewrite_node",
    "looks_like_followup",
    "rewrite_question",
]
