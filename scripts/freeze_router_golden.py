"""冻结 router 现状真值（P4 动态化接线前快照）。

改动 router 前先跑一次：把当前 5 个 route_* 工具的 name/description（LLM 实际
看到的字节）与 ROUTER_SYSTEM_PROMPT 原样写入 test/data/ 下 golden 文件，
作为动态化生成器 byte-equal 对拍基准（防止行为漂移）。

用法:
    python scripts/freeze_router_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.router as router  # noqa: E402
DATA = ROOT / "test" / "data"


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)

    tools = [
        {"name": t.name, "description": t.description, "args": t.args}
        for t in router.ROUTER_TOOLS
    ]
    (DATA / "router_tools_golden.json").write_text(
        json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA / "router_system_prompt.txt").write_text(
        router.ROUTER_SYSTEM_PROMPT, encoding="utf-8"
    )
    print(f"freeze ok: {len(tools)} tools + system prompt -> {DATA}")


if __name__ == "__main__":
    main()
