# 功能：通过 HTTP 调用远端 MinerU 解析服务（对齐部署补充方案 §4.1 协议）。
"""MinerU 远程解析客户端 — 调用服务器上的 FastAPI 解析服务。

与 MinerUParser（本地 magic-pdf CLI）互补：本类只依赖 HTTP 协议
（POST /parse、GET /health），不感知 MinerU 装在哪台机器。换部署位置只改
base_url，代码零改动（对齐部署补充方案 §4.2）。

协议（见 docs/execution_plans/pdf_tiered_server_deploy_supplement_20260831.md §4.1）：
  POST /parse    multipart/file 子 PDF + page_range? → {"markdown": ...}
  GET  /health   → {"status": "ok", "parser": "mineru", "parser_version": ...}
  Header: Authorization: Bearer <token>

缓存 key（本地/服务器双侧一致，见 §4.3）：
  md5(source_pdf_bytes) | page_range | parser_version
本类在本地也落一份缓存，避免同一子 PDF 重复上传。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from core.config.settings import Settings

logger = logging.getLogger(__name__)

# P5：health 探测用短超时（秒）。服务不可达时须**快速失败**并回退「上次已知
# 版本」，否则默认的解析超时（mineru_api_timeout，默认 1800s）会让离线重跑
# 长时间挂起。
_PROBE_TIMEOUT_SEC = 5.0
# P5：持久化的 parser_version 文件名（落在本地缓存目录）。
_VERSION_FILE = "_parser_version.txt"

# P5 加固：进程级版本 memo 的 TTL（秒）。**为什么需要**：服务端是单 worker +
# 阻塞 `subprocess.run`（`deploy/mineru/service/app.py`），解析期间事件循环被
# 占住 → `/health` 无法响应 → 客户端探测**必然**等满 5s 超时才回退。一个多
# Tier C 范围的 PDF 会调用 parse() 多次 → N × 5s 纯空转。memo 后每进程每 TTL
# 只探测一次。（版本只在 MinerU 升级时变，TTL 内复用旧值只会多一次重解析，不会出错。）
_VERSION_MEMO_TTL_SEC = float(os.getenv("MINERU_VERSION_MEMO_TTL", "300"))
_version_memo: dict[str, tuple[float, str]] = {}
_version_memo_lock = threading.Lock()


def reset_version_memo() -> None:
    """清空版本 memo（测试用）。"""
    with _version_memo_lock:
        _version_memo.clear()


def _memo_get(base_url: str) -> Optional[str]:
    with _version_memo_lock:
        hit = _version_memo.get(base_url)
        if hit is None:
            return None
        stamped_at, version = hit
        # 用 >= ：Windows 上 time.monotonic() 分辨率约 15ms，相邻两次调用可能得到
        # 同一个值（差值 0），用 > 会让 TTL=0 的用例判不出过期。
        if time.monotonic() - stamped_at >= _VERSION_MEMO_TTL_SEC:
            _version_memo.pop(base_url, None)
            return None
        return version


def _memo_put(base_url: str, version: str) -> None:
    with _version_memo_lock:
        _version_memo[base_url] = (time.monotonic(), version)


class MinerUApiParser:
    """通过 HTTP 调用 MinerU 解析服务，把（子）PDF 解析为 Markdown。"""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self._settings = settings
        self._base_url = (base_url or settings.mineru_api_base_url).rstrip("/")
        self._token = token if token is not None else settings.mineru_api_token
        self._timeout = (
            timeout if timeout is not None else settings.mineru_api_timeout
        )
        # P5：服务不可达时的「上次已知版本」懒加载 memo（避免反复扫盘）。
        self._fallback_version: Optional[str] = None

    @property
    def base_url(self) -> str:
        return self._base_url

    # ── 对外接口 ───────────────────────────────────────────

    def health(self) -> dict:
        """GET /health，返回服务状态（含 parser_version）。"""
        return self._request("GET", "/health")

    def parse(
        self,
        pdf_path: str | Path,
        *,
        page_range: Optional[str] = None,
    ) -> str:
        """POST /parse 上传（子）PDF，返回该范围的 Markdown。

        Args:
            pdf_path: 源 PDF 或子 PDF 的绝对路径。
            page_range: 可选页范围（透传给服务，同时参与本地缓存 key）。

        Returns:
            解析出的 Markdown 字符串。

        Raises:
            FileNotFoundError: PDF 文件不存在。
            RuntimeError: 未配置服务地址、服务不可达、解析失败或返回空。
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if not self._base_url:
            raise RuntimeError(
                "未配置 MinerU 解析服务地址（MINERU_API_BASE_URL / "
                "PDF_PARSE_BASE_URL）。请先完成服务器部署并在 .env 配置。"
            )

        parser_version = self._parser_version()
        cached = self._find_cached(pdf_path, page_range, parser_version)
        if cached is not None:
            logger.info("命中远程解析缓存: %s", pdf_path.name)
            return cached.read_text(encoding="utf-8")

        payload = self._request(
            "POST", "/parse", file_path=pdf_path, page_range=page_range
        )
        markdown = (payload or {}).get("markdown") or ""
        if not markdown.strip():
            raise RuntimeError(f"MinerU 服务返回空 Markdown: {pdf_path.name}")
        self._write_cache(pdf_path, page_range, parser_version, markdown)
        return markdown

    # ── 内部实现 ───────────────────────────────────────────

    def resolve_parser_version(self) -> str:
        """解析 parser_version：探测成功则持久化；失败则回退「上次已知版本」。

        P5：缓存键含 parser_version。服务不可达时若返回空串，本地缓存会**全部
        miss** → 转 HTTP → 失败 → 无 `allow_partial` 时整本解析失败。回退到
        上次已知值后，离线重跑可全部命中本地缓存。

        P5 加固：结果按 ``_VERSION_MEMO_TTL_SEC`` 进程内 memo —— 服务端解析期间
        `/health` 不可达，逐个 parse 探测会各等满 5s 超时（见模块常量注释）。
        """
        memoized = _memo_get(self._base_url)
        if memoized is not None:
            return memoized
        try:
            payload = self._request("GET", "/health", timeout=_PROBE_TIMEOUT_SEC)
            version = str(payload.get("parser_version") or "")
        except Exception:  # noqa: BLE001  不可达/超时 → 走回退
            version = ""
        if version:
            self._persist_version(version)
        else:
            version = self._fallback_parser_version()
        _memo_put(self._base_url, version)
        return version

    def _parser_version(self) -> str:
        """（内部）解析 parser_version —— 等价于 resolve_parser_version。"""
        return self.resolve_parser_version()

    def _version_file(self) -> Path:
        return self._cache_dir() / _VERSION_FILE

    def _persist_version(self, version: str) -> None:
        try:
            self._cache_dir().mkdir(parents=True, exist_ok=True)
            self._version_file().write_text(version, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001  持久化失败不影响解析
            logger.warning("持久化 parser_version 失败: %s", exc)

    def _fallback_parser_version(self) -> str:
        """服务不可达时的版本回退（进程内 memo，避免反复扫盘）。

        来源优先级：`_parser_version.txt` → 任一缓存 sidecar `.json` 的
        `parser_version`（缓存写入时记录）→ 空串（无历史可依）。
        """
        if self._fallback_version is not None:
            return self._fallback_version
        version = ""
        try:
            vf = self._version_file()
            if vf.exists():
                version = vf.read_text(encoding="utf-8").strip()
        except Exception:  # noqa: BLE001
            version = ""
        if not version:
            version = self._scan_sidecars_for_version()
            if version:
                self._persist_version(version)
        self._fallback_version = version
        return version

    def _scan_sidecars_for_version(self) -> str:
        """从缓存 sidecar `.json` 恢复 parser_version（缓存写入时记录过）。"""
        try:
            for meta in sorted(self._cache_dir().glob("*.json")):
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001  单条损坏跳过
                    continue
                version = str(data.get("parser_version") or "").strip()
                if version:
                    logger.info(
                        "离线回退：从缓存 sidecar 恢复 parser_version=%s", version
                    )
                    return version
        except Exception:  # noqa: BLE001
            return ""
        return ""

    def _request(
        self,
        method: str,
        path: str,
        *,
        file_path: Optional[Path] = None,
        page_range: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        effective_timeout = self._timeout if timeout is None else timeout
        try:
            if method == "GET":
                resp = httpx.get(url, headers=headers, timeout=effective_timeout)
            else:
                file_bytes = Path(file_path).read_bytes()
                resp = httpx.post(
                    url,
                    headers=headers,
                    data={"page_range": page_range or ""},
                    files={
                        "file": (
                            Path(file_path).name,
                            file_bytes,
                            "application/pdf",
                        )
                    },
                    timeout=effective_timeout,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MinerU 服务不可达 ({url}): {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"MinerU 服务返回 {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    def _cache_key(self, pdf_path: Path, page_range: Optional[str], version: str) -> str:
        # 用组合串再哈希一次得到纯 hex 文件名，避免 `|` 等在 Windows 文件系统非法。
        digest = hashlib.md5(pdf_path.read_bytes()).hexdigest()
        composite = f"{digest}|{page_range or ''}|{version}"
        return hashlib.md5(composite.encode("utf-8")).hexdigest()

    def _cache_dir(self) -> Path:
        return Path(self._settings.mineru_output_dir) / "_mineru_api_cache"

    def _find_cached(
        self, pdf_path: Path, page_range: Optional[str], version: str
    ) -> Optional[Path]:
        md = self._cache_dir() / f"{self._cache_key(pdf_path, page_range, version)}.md"
        return md if md.exists() else None

    def _write_cache(
        self,
        pdf_path: Path,
        page_range: Optional[str],
        version: str,
        markdown: str,
    ) -> None:
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        md = cache_dir / f"{self._cache_key(pdf_path, page_range, version)}.md"
        md.write_text(markdown, encoding="utf-8")
        # 落一份轻量元数据，便于排查（与 .md 同前缀）
        meta = {
            "source": str(pdf_path),
            "page_range": page_range,
            "parser_version": version,
            "bytes": md.stat().st_size,
        }
        md.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
