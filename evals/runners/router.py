"""路由层评测跑批（E5-2）— 把金标逐题喂给 router 节点。

与检索层评测不同，这里要**真实调 LLM**（L3 分类），故：
  - 支持并发（`workers`，默认 8）——router 调用是纯 I/O 等待；
  - 单题失败不中断整批（记 `error`，由 `evals.metrics.router` 计入混淆矩阵）；
  - **不 import agent/**（router 节点由调用方注入），保持评测包与运行时包的边界。

用法（脚本侧）：
    from evals.runners.router import run_router
    got = run_router(samples, router_node, workers=8)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Sequence

from ..datasets.rag import EvalSample


def run_router(
    samples: Sequence[EvalSample],
    route_fn: Callable[[str], str],
    *,
    workers: int = 8,
    progress_every: int = 50,
    on_progress: Callable[[int, int], None] | None = None,
) -> Dict[str, str]:
    """并发跑一遍路由，返回 `{question: 路由结果}`。

    Args:
        samples: 金标样本（只用 `question`）。
        route_fn: 单题路由函数（question → 能力 id）；由调用方注入 router 节点。
        workers: 并发度（1 = 串行）。
        progress_every: 每完成 N 条回调一次进度。
        on_progress: 进度回调 `(done, total)`。
    """
    questions: List[str] = [s.question for s in samples]
    total = len(questions)
    results: Dict[str, str] = {}
    done = 0

    def _one(q: str) -> tuple[str, str]:
        try:
            return q, route_fn(q)
        except Exception:  # noqa: BLE001  单题失败降级，不影响整批
            return q, "error"

    if workers <= 1:
        for q in questions:
            key, value = _one(q)
            results[key] = value
            done += 1
            if on_progress and done % progress_every == 0:
                on_progress(done, total)
        if on_progress:
            on_progress(done, total)
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, value in pool.map(_one, questions):
            results[key] = value
            done += 1
            if on_progress and done % progress_every == 0:
                on_progress(done, total)
    if on_progress:
        on_progress(done, total)
    return results
