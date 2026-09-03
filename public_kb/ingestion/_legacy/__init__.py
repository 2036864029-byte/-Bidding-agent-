"""ingestion._legacy — 旧 Source 流水线归档（P6 结构收敛）。

engine 与 handoff 已切到三段式 FileIngestionPipeline / MilvusSink；
本目录保留旧 Source 基类与 pipeline_legacy，仅供 CLI ``--legacy`` CSV 回滚与
对应测试使用，不再参与新入库路径。过渡期结束后可整体移除。
"""
