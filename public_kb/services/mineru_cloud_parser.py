# 功能：通过 HTTP 调用 MinerU 官方 API（mineru.net）把 PDF 解析为 Markdown。
"""MinerU 官方 API 客户端 —— 云端部署用，本机无需安装 MinerU / GPU。

为什么单独一个类：官方 API 与自建服务（MinerUApiParser）**协议不同**。
自建服务是「POST /parse 同步返回 {"markdown": ...}」；官方是异步任务制：

  1. POST {base}/file-urls/batch                 申请上传链接 → {batch_id, file_urls[]}
  2. PUT  file_urls[0]                           直接上传 PDF 字节（**不要**设 Content-Type）
  3. GET  {base}/extract-results/batch/{batch_id} 轮询 → extract_result[].full_zip_url
  4. GET  full_zip_url                           下载 zip，取其中 full.md

Header 统一 `Authorization: Bearer <token>`，Token 复用 MINERU_API_TOKEN。
协议文档：https://mineru.net/apiManage/docs

对外暴露的接口与 MinerUApiParser 一致（`parse(pdf_path, *, page_range=)` +
`resolve_parser_version()`），因此 `PDF_PARSE_BACKEND` 可以在两者之间热切换，
`parser_factory` 与 `PdfRouter` 无需感知差异。

缓存 key 对齐 MinerUApiParser：md5(pdf_bytes) | page_range | parser_version。
官方没有版本端点，用 model_version 合成稳定版本串（改 model_version 即失效）。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from core.config.settings import Settings

logger = logging.getLogger(__name__)

# 官方 zip 产物里的 Markdown 文件名（见 output_files 规范：full.md）
_MARKDOWN_MEMBER = "full.md"
# 任务终态；其余（waiting-file / pending / running / converting）都继续轮询
_STATE_DONE = "done"
_STATE_FAILED = "failed"


class MinerUCloudParser:
    """调 MinerU 官方 API，把（子）PDF 解析为 Markdown。"""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[int] = None,
        model_version: Optional[str] = None,
    ) -> None:
        """构造官方 API 客户端。

        Args:
            settings: 全局配置。
            base_url: 覆盖官方 API 基址（默认 https://mineru.net/api/v4）。
            token: 覆盖 Token（默认取 settings.mineru_api_token）。
            timeout: 单次 HTTP 请求超时秒数（默认 settings.mineru_api_timeout）。
            poll_interval: 轮询间隔秒数（默认 settings.mineru_official_poll_interval）。
            poll_timeout: 轮询总超时秒数（默认 settings.mineru_official_poll_timeout）。
            model_version: pipeline / vlm / MinerU-HTML（默认取 settings）。
        """
        self._settings = settings
        self._base_url = (
            base_url or settings.mineru_official_base_url
        ).rstrip("/")
        self._token = token if token is not None else settings.mineru_api_token
        self._timeout = (
            timeout if timeout is not None else settings.mineru_api_timeout
        )
        self._poll_interval = (
            poll_interval
            if poll_interval is not None
            else settings.mineru_official_poll_interval
        )
        self._poll_timeout = (
            poll_timeout
            if poll_timeout is not None
            else settings.mineru_official_poll_timeout
        )
        self._model_version = model_version or settings.mineru_official_model_version

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model_version(self) -> str:
        return self._model_version

    # ── 对外接口 ───────────────────────────────────────────

    def health(self) -> dict:
        """配置自检（**不联网**：官方无 /health 端点，故不伪造探测）。

        返回格式对齐 MinerUApiParser.health()，便于上层统一打印。
        """
        return {
            "status": "ok" if self._token else "unconfigured",
            "parser": "mineru-official",
            "parser_version": self.resolve_parser_version(),
            "base_url": self._base_url,
        }

    def resolve_parser_version(self) -> str:
        """解析器版本串（进缓存 key）。

        官方 API 不暴露模型版本，用 `official-<model_version>` 作为稳定标识：
        只有改动 model_version 时才让缓存失效，避免每次解析都重打 API。
        """
        return f"official-{self._model_version}"

    def parse(
        self,
        pdf_path: str | Path,
        *,
        page_range: Optional[str] = None,
    ) -> str:
        """上传（子）PDF 到官方 API 并返回 Markdown。

        Args:
            pdf_path: 源 PDF 或子 PDF 的绝对路径。
            page_range: 可选页范围。**仅参与本地缓存 key，不发给官方 API** ——
                见下方 `_create_batch` 的说明（与自建服务语义保持一致）。

        Returns:
            解析出的 Markdown 字符串。

        Raises:
            FileNotFoundError: PDF 文件不存在。
            RuntimeError: 未配置 Token、接口报错、任务失败、轮询超时或产物缺失。
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if not self._token:
            raise RuntimeError(
                "未配置 MinerU 官方 API Token（MINERU_API_TOKEN）。"
                "请到 https://mineru.net/apiManage 申请后写入 .env。"
            )

        version = self.resolve_parser_version()
        cached = self._find_cached(pdf_path, page_range, version)
        if cached is not None:
            logger.info("命中官方解析缓存: %s", pdf_path.name)
            return cached.read_text(encoding="utf-8")

        # 官方流程：申请链接 → 上传 → 轮询 → 下载 zip
        batch_id, upload_url = self._create_batch(pdf_path, page_range)
        self._upload(upload_url, pdf_path)
        zip_url = self._poll_result(batch_id, pdf_path)
        markdown = self._download_markdown(zip_url)

        if not markdown.strip():
            raise RuntimeError(f"MinerU 官方 API 返回空 Markdown: {pdf_path.name}")
        self._write_cache(pdf_path, page_range, version, markdown)
        return markdown

    # ── 协议三步 ───────────────────────────────────────────

    def _create_batch(
        self, pdf_path: Path, page_range: Optional[str]
    ) -> tuple[str, str]:
        """第 1 步：申请上传链接，返回 (batch_id, upload_url)。

        ⚠️ **不把 page_range 映射成官方 `page_ranges`**。三档路由传上来的是
        `write_subpdf()` 切好的子 PDF（`pdf_mineru_router.parse_ranges` 调
        `parser.parse(subpdf_path)`，不带 page_range），页范围是**相对源 PDF**
        的；再把同一个范围声明给官方 API，会被当作相对**子 PDF**的页码二次
        裁剪 —— 结果是内容错位或直接空返回。这与自建服务一致：
        `deploy/mineru/service/app.py` 也只把 page_range 用在缓存键上。

        Args:
            pdf_path: 待上传的 PDF（通常是已切好的子 PDF）。
            page_range: 仅用于日志/溯源的原始页范围，不下发。
        """
        file_entry: dict = {"name": pdf_path.name}
        body = {
            "files": [file_entry],
            "model_version": self._model_version,
            "language": self._settings.mineru_official_language,
            "enable_table": True,
            "enable_formula": True,
        }
        if page_range:
            logger.info(
                "子 PDF %s 来自页范围 %s（范围不下发，官方按子 PDF 全页解析）",
                pdf_path.name,
                page_range,
            )
        payload = self._request("POST", "/file-urls/batch", json_body=body)
        data = payload.get("data") or {}
        batch_id = str(data.get("batch_id") or "")
        urls = data.get("file_urls") or []
        if not batch_id or not urls:
            raise RuntimeError(f"MinerU 官方 API 未返回上传链接: {payload}")
        return batch_id, str(urls[0])

    def _upload(self, upload_url: str, pdf_path: Path) -> None:
        """第 2 步：PUT 上传文件字节。

        ⚠️ 官方明确要求上传时**不要**设 Content-Type —— 签名 URL 对头部敏感，
        带上 application/pdf 会被对象存储拒签。
        """
        try:
            resp = httpx.put(
                upload_url,
                content=pdf_path.read_bytes(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MinerU 上传失败 ({pdf_path.name}): {exc}") from exc
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"MinerU 上传返回 {resp.status_code}: {getattr(resp, 'text', '')[:300]}"
            )

    def _poll_result(self, batch_id: str, pdf_path: Path) -> str:
        """第 3 步：轮询 batch 结果，返回 full_zip_url。"""
        path = f"/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + self._poll_timeout
        last_state = ""
        while True:
            payload = self._request("GET", path)
            result = self._first_result(payload, pdf_path)
            state = str(result.get("state") or "")
            if state != last_state:
                logger.info(
                    "MinerU 官方解析中: %s → %s", pdf_path.name, state or "unknown"
                )
                last_state = state
            if state == _STATE_DONE:
                zip_url = str(result.get("full_zip_url") or "")
                if not zip_url:
                    raise RuntimeError(
                        f"MinerU 官方 API 任务完成但无 full_zip_url: {result}"
                    )
                return zip_url
            if state == _STATE_FAILED:
                raise RuntimeError(
                    f"MinerU 官方解析失败: {result.get('err_msg') or '未知错误'}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"MinerU 官方解析轮询超时 ({self._poll_timeout}s)，"
                    f"batch_id={batch_id}，最后状态={state or 'unknown'}"
                )
            time.sleep(self._poll_interval)

    def _download_markdown(self, zip_url: str) -> str:
        """第 4 步：下载结果 zip 并取出 Markdown 正文。"""
        try:
            resp = httpx.get(zip_url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MinerU 结果包下载失败: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"MinerU 结果包下载返回 {resp.status_code}")
        return extract_markdown_from_zip(resp.content)

    # ── HTTP 与缓存 ────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
    ) -> dict:
        """发 JSON 请求并校验 code==0，返回整个响应体。"""
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }
        try:
            if method == "GET":
                resp = httpx.get(url, headers=headers, timeout=self._timeout)
            else:
                resp = httpx.post(
                    url, headers=headers, json=json_body, timeout=self._timeout
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MinerU 官方 API 不可达 ({url}): {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"MinerU 官方 API 返回 {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        if payload.get("code") != 0:
            raise RuntimeError(
                f"MinerU 官方 API 报错 code={payload.get('code')}: "
                f"{payload.get('msg') or payload}"
            )
        return payload

    @staticmethod
    def _first_result(payload: dict, pdf_path: Path) -> dict:
        """从 batch 结果里取本次文件那一条（单文件提交，取第一条即可）。"""
        data = payload.get("data") or {}
        results = data.get("extract_result") or []
        if not results:
            raise RuntimeError(f"MinerU 官方 API 未返回解析结果: {payload}")
        # 优先按文件名匹配；官方回填的 file_name 可能被重命名，故兜底取首条
        for item in results:
            if item.get("file_name") == pdf_path.name:
                return item
        return results[0]

    def _cache_key(self, pdf_path: Path, page_range: Optional[str], version: str) -> str:
        digest = hashlib.md5(pdf_path.read_bytes()).hexdigest()
        composite = f"{digest}|{page_range or ''}|{version}"
        return hashlib.md5(composite.encode("utf-8")).hexdigest()

    def _cache_dir(self) -> Path:
        return Path(self._settings.mineru_output_dir) / "_mineru_cloud_cache"

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
        meta = {
            "source": str(pdf_path),
            "page_range": page_range,
            "parser_version": version,
            "backend": "official",
            "bytes": md.stat().st_size,
        }
        md.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def extract_markdown_from_zip(zip_bytes: bytes) -> str:
    """从官方结果 zip 里取出 Markdown 正文。

    优先 `full.md`（官方规范）；若不存在则退而取第一个 `.md`（官方产物目录
    可能因版本调整改名，退让比整本解析失败更划算）。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            target = next(
                (n for n in names if n.endswith(_MARKDOWN_MEMBER) or n == _MARKDOWN_MEMBER),
                None,
            )
            if target is None:
                target = next((n for n in names if n.lower().endswith(".md")), None)
            if target is None:
                raise RuntimeError(
                    f"MinerU 结果包内未找到 Markdown 文件，含: {names[:10]}"
                )
            return zf.read(target).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"MinerU 结果包不是合法 zip: {exc}") from exc
