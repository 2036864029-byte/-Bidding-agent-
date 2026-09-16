# 功能：Milvus 集合命名单一来源（集中治理散落的字面量/重复 env 默认值）。
"""集合命名治理（Phase 0 / A-9，解 R10）。

集中声明两个集合的默认命名；env 覆盖仍优先（与现状行为一致）：
  - 公共知识库集合：Settings.collection_name（env MILVUS_COLLECTION，默认 public_kb）
  - 价格/标讯 MySQL 语义集合：MYSQL_SEMANTIC_COLLECTION（默认 mysql_price_semantic）

semantic.py / schema_registry 等以本模块默认值取代各自重复的字面量默认，
避免两处维护导致集合名漂移。
"""

# 公共知识库文本块集合（法规 PDF 切片）
KB_PUBLIC_COLLECTION = "public_kb"

# MySQL 结构化数据语义集合（企业/处罚/标讯 → 向量召回）
PRICE_SEMANTIC_COLLECTION = "mysql_price_semantic"

# 招标公告语义集合（项目概况相似检索与金额参考，2026-09-16 方案）：
# 每条 = 一篇中标公告（announcement_id 主键），向量文本 = 项目名 + 产品清单 + 正文头段。
# 与 public_kb / mysql_price_semantic 完全独立，检索走独立端点 POST /inquiry/reference。
PROJECT_ANNOUNCEMENT_COLLECTION = "project_announcement_semantic"

# 用户临时文档会话隔离集合前缀（9.3 Track D / D-4 扩展点）：
# doc_qa 如需临时检索，按 ``DOC_KV_PREFIX + session_id`` 建集合，永不写入公共集合。
DOC_KV_PREFIX = "doc_kv_"
