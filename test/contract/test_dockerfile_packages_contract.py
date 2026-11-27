"""镜像打包契约 —— 每个运行期顶层包都必须被 Dockerfile COPY 进去。

**为什么需要**（2026-12 实测踩到的真实事故）：
`api/server.py` 在 `create_app()` 里**无条件**导入 `channels.feishu.push`
（未配 `FEISHU_APP_ID` 时该推送器是 no-op，所以本地不配也一直好用）。
而 `deploy/api/Dockerfile` 的 COPY 列表是手写的 —— 新包 `channels/` 建好后
没人记得加，结果：

    ModuleNotFoundError: No module named 'channels'
    File "/app/api/server.py", line 813, in create_app

容器**启动即崩**。这个失效在本地永远看不到：`pytest` 跑的是源码树，
`npm run build` 也不碰它 —— 只有 `docker build` + `up` 才暴露，而那一刻
通常已经在服务器上了。

本测试把「顶层包集合」与「Dockerfile 的 COPY 列表」锚在一起：**新增包忘了拷，
CI 立即红**。

**为什么 allowlist 要显式写**：把"哪些包不进镜像"变成需要改代码才能变的决定，
而不是靠某个人的记忆。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "deploy" / "api" / "Dockerfile"
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"

#: 有意**不**进运行期镜像的顶层包（评测/开发工具，运行期代码不得 import；
#: 该约束由 test/core/test_dependency_isolation.py 从另一侧看守）。
_DEV_ONLY_PACKAGES = {"evals"}


def _runtime_packages() -> set[str]:
    """仓库根目录下带 `__init__.py` 的顶层包，去掉 dev-only。"""
    return {
        p.name
        for p in _REPO_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file() and p.name not in _DEV_ONLY_PACKAGES
    }


def _dockerfile_copied_packages() -> set[str]:
    """Dockerfile 里 `COPY <pkg>/ ...` 拷进去的包名。"""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(r"^COPY\s+([A-Za-z_][A-Za-z0-9_]*)/\s", text, re.MULTILINE)
    }


def test_every_runtime_package_is_copied_into_image():
    """顶层运行期包 == Dockerfile COPY 的包（全等，不是包含）。"""
    runtime = _runtime_packages()
    copied = _dockerfile_copied_packages()

    missing = sorted(runtime - copied)
    assert not missing, (
        f"这些顶层包在仓库里存在，但没被 COPY 进镜像：{missing}\n"
        f"  后果：容器启动即 ModuleNotFoundError（不是功能降级）。\n"
        f"  修法：在 deploy/api/Dockerfile 补 `COPY <pkg>/ ./<pkg>/`。\n"
        f"  当前已 COPY：{sorted(copied)}"
    )


def test_no_copied_package_is_missing_on_disk():
    """反向：Dockerfile COPY 的目录必须真实存在，否则 docker build 直接失败。"""
    missing = sorted(p for p in _dockerfile_copied_packages() if not (_REPO_ROOT / p).is_dir())
    assert not missing, (
        f"deploy/api/Dockerfile 里 COPY 了不存在的目录：{missing}\n"
        f"  docker build 会报 failed to compute cache key / not found。"
    )


def test_dockerignore_does_not_exclude_runtime_packages():
    """.dockerignore 不得把运行期包排除出构建上下文（排除后 COPY 会失败）。"""
    ignored: set[str] = set()
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ignored.add(line.rstrip("/").lstrip("./"))

    clash = sorted(_runtime_packages() & ignored)
    assert not clash, (
        f".dockerignore 排除了运行期包：{clash}\n"
        f"  排除后即使 Dockerfile 写了 COPY，构建时也拿不到文件。"
    )
