# 功能：core/retrieval 包。
"""core.retrieval — 共享召回降级/熔断状态机（方案 Phase 3 / D-1、D-6）。

price 5 级召回链（dense→FULLTEXT→LIKE→FULL_SCAN）下沉后接入本状态机；
FULL_SCAN 默认禁入（allow_scan=False），防长尾裸全表扫描拖垮节点。
"""
