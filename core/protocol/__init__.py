# 功能：core/protocol 包。
"""core.protocol — 出口薄协议层（方案 Phase 2 / C-1~C-2）。

对外统一回复模型（API/CLI 出口），不承载业务模板——price 域渲染模板属
price_engine/rendering（见附录 C-0 修正），此处仅建 AgentReply + renderers。
"""
