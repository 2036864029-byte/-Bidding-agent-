"""前端构建环境契约 —— 生产构建必须自带 `VITE_CHAT_MODE=api`，且不得烘死后端地址。

**为什么需要**（本项目实测到的真实事故形态）：
`VITE_CHAT_MODE` 是**构建期**变量。它原本只写在 `frontend/.env.local`，而该文件被
gitignore —— 本地 `npm run build` 得到 `chat:"api"`，服务器上（git clone / rsync
拿不到 `.env.local`）构建出来却是 `chat:"mock"`，**对话栏显示内置演示样例**。
这种失效"不报错、不告警、功能看着正常"，只有真人去问一句才发现是假数据。

实测证据（修复前后各构建一次）：
  - 两个 env 文件都不存在 → 产物含运行时三元
    `(...VITE_CHAT_MODE)==="api"?"api":"mock"`，求值为 `mock`；
  - 只有 `.env.production` → 折叠为字面量 `chat:"api"`。

**为什么还断言源码里的键名**：把 env 键锚在源码上。有人把 `api.ts` 里的
`VITE_CHAT_MODE` 改名后，`.env.production` 会静默变成无效配置 —— 又是一个
"看着没问题"的失效。这条断言让改名必须先改这里。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PROD = _REPO_ROOT / "frontend" / ".env.production"
_API_TS = _REPO_ROOT / "frontend" / "src" / "services" / "api.ts"
_BACKEND_TS = _REPO_ROOT / "frontend" / "src" / "services" / "backend.ts"
_NGINX_CONF = _REPO_ROOT / "deploy" / "web" / "nginx.conf"


def _parse_dotenv(path: Path) -> dict[str, str]:
    """解析 .env 文件为 {KEY: value}（注释与空行跳过）。"""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


# ── 构建期环境变量 ─────────────────────────────────────────


def test_env_production_is_committed():
    """`.env.production` 必须存在且入库（不能只靠被 gitignore 的 `.env.local`）。"""
    assert _ENV_PROD.exists(), (
        "缺少 frontend/.env.production。\n"
        "  后果：服务器上构建出来的前端 chat 数据源会退化成 'mock'，\n"
        "        对话栏显示内置演示数据。修法：补回该文件，写入 VITE_CHAT_MODE=api。"
    )


def test_chat_mode_is_api_in_production_build():
    """生产构建的对话数据源必须是 api。"""
    env = _parse_dotenv(_ENV_PROD)
    assert env.get("VITE_CHAT_MODE") == "api", (
        f"frontend/.env.production 里 VITE_CHAT_MODE 应为 'api'，实际为 "
        f"{env.get('VITE_CHAT_MODE')!r}。\n"
        "  缺省时 `api.ts` 会退回 'mock'（内置演示数据），上线不报错但全是假数据。"
    )


def test_api_base_not_baked_into_production():
    """生产构建不得设置 VITE_API_BASE —— 必须走同域 `/api`。

    空值同样有害：`VITE_API_BASE ?? '/api'` 里 `??` 只在 null/undefined 时回退，
    空字符串会让 BASE 变成 ''，所有请求丢掉 `/api` 前缀。
    """
    env = _parse_dotenv(_ENV_PROD)
    assert "VITE_API_BASE" not in env, (
        "frontend/.env.production 不应出现 VITE_API_BASE。\n"
        "  设了域名 → 烘进 bundle，用户浏览器会去连该地址（本地构建时是 127.0.0.1，全站白屏）；\n"
        "  设成空值 → `??` 不回退，BASE 变 ''，请求丢失 /api 前缀。\n"
        "  正确做法：整个键都不要出现，由 nginx 反代同域 /api。"
    )


def test_env_keys_match_frontend_source():
    """env 键名必须与前端源码里实际读取的键一致（锚住，防改名后静默失效）。"""
    api_src = _API_TS.read_text(encoding="utf-8")
    backend_src = _BACKEND_TS.read_text(encoding="utf-8")
    assert "VITE_CHAT_MODE" in api_src, (
        "frontend/src/services/api.ts 已不再读取 VITE_CHAT_MODE —— "
        "若改名，请同步更新 .env.production 与本测试。"
    )
    assert "VITE_API_BASE" in backend_src, (
        "frontend/src/services/backend.ts 已不再读取 VITE_API_BASE —— "
        "若改名，请同步更新本测试与部署手册 §4。"
    )


# ── nginx 反向代理 ─────────────────────────────────────────


def _nginx_text() -> str:
    return _NGINX_CONF.read_text(encoding="utf-8")


def test_nginx_disables_proxy_buffering_for_sse():
    """SSE 必须关缓冲，否则流式对话会攒到最后一次性吐出。"""
    assert "proxy_buffering" in _nginx_text(), "deploy/web/nginx.conf 缺少 proxy_buffering 配置"


def test_nginx_enables_gzip_for_javascript():
    """必须显式 gzip js/css —— Debian/Ubuntu 默认 gzip_types 只含 text/html。

    不配这一条，入口 chunk 会以未压缩的 ~800 KB 传输（gzip 后约 244 KB）。
    """
    text = _nginx_text()
    assert "gzip " in text or "gzip  " in text, "deploy/web/nginx.conf 未开启 gzip"
    assert "application/javascript" in text, (
        "deploy/web/nginx.conf 的 gzip_types 未包含 application/javascript。\n"
        "  默认配置只压缩 text/html，js/css 会以未压缩体积传输。"
    )
