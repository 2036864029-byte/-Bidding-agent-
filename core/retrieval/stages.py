# 功能：召回阶段的具体实现容器（占位，Phase 3 / D-1）。
"""stages — RecallStage 具体实现（VectorStage/FulltextStage/LikeStage/ScanStage）。

⚠️ D-6 实现偏差：price 特有的 SQL 阶段适配放在 **price_engine/recall_stages.py**
（_SqlRecallStage / _SplitKeywordStage / build_recall_stages），而非本文件——因为
这些阶段强依赖 price_engine 的 SearchIntent / sql_builders，放 core 会制造
core → price_engine 反向依赖。本文件保留为通用占位（与 A-8 迁移延后的取舍一致）。
core 侧只提供协议（base.py）与熔断状态机（pipeline.py），保持通用。
"""
