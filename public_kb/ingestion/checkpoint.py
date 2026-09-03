"""StageCheckpoint — 每文件阶段进度 + 中间产物缓存（方案 Phase 5 / E-10）。

断点续跑：``run_manifest.json`` 记录每个文件到达的阶段（A=normalize / C=sink）；
Stage A 的 markdown IR 落盘为中间产物（artifacts/），供 ``--rerun-from=B`` 复用——
PDF 解析（MinerU）是最贵的一步，解析器升级/重跑 B+C 时无需重烧。

语义：
  - ``--resume``：跳过 C 已完成的文件；
  - ``--rerun-from=B``：A 产物存在则复用（跳过 normalize），重做 B+C。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STAGE_A = "A"  # normalize（pdf 解析最贵）
STAGE_C = "C"  # sink


class StageCheckpoint:
    """按文件记录阶段进度，缓存 Stage A 中间产物。"""

    def __init__(self, work_dir: str | Path) -> None:
        self._dir = Path(work_dir)
        self._artifacts_dir = self._dir / "artifacts"
        self._manifest_path = self._dir / "run_manifest.json"
        self._manifest: Dict[str, dict] = {}
        self._load()

    # ── 阶段查询 ───────────────────────────────────────────

    def is_done(self, path: str | Path, stage: str) -> bool:
        entry = self._manifest.get(str(Path(path).resolve()))
        return bool(entry and entry.get("stages", {}).get(stage))

    # ── 阶段标记 ───────────────────────────────────────────

    def mark(self, path: str | Path, stage: str) -> None:
        key = str(Path(path).resolve())
        entry = self._manifest.setdefault(key, {"stages": {}})
        entry["stages"][stage] = True
        self._persist()

    # ── 中间产物（Stage A markdown IR）─────────────────────

    def _artifact_key(self, path: str | Path) -> str:
        return hashlib.md5(str(Path(path).resolve()).encode("utf-8")).hexdigest()

    def has_artifact(self, path: str | Path) -> bool:
        return (self._artifacts_dir / f"{self._artifact_key(path)}.md").exists()

    def save_artifact(self, path: str | Path, content: str) -> Path:
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        p = self._artifacts_dir / f"{self._artifact_key(path)}.md"
        p.write_text(content, encoding="utf-8")
        return p

    def load_artifact(self, path: str | Path) -> str:
        return (self._artifacts_dir / f"{self._artifact_key(path)}.md").read_text(
            encoding="utf-8"
        )

    # ── 持久化 ─────────────────────────────────────────────

    def _load(self) -> None:
        if not self._manifest_path.exists():
            return
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._manifest = data
        except json.JSONDecodeError:
            logger.warning("run_manifest.json 损坏，忽略旧进度")
            self._manifest = {}

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
