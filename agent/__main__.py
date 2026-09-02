"""
Agent CLI 薄转发入口 — CLI 逻辑已收编 api.cli（方案 Phase 2 / C-6）。

保留 ``python -m agent`` 命令；实际实现在 api/cli.py，此文件仅做入口转发。
``python -m agent --question "..."`` 与 ``python -m api.cli --question "..."`` 行为一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 path（确保可独立运行）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
