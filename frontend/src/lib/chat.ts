import type { Citation } from '@/types/chat';

/**
 * 对话模块的纯逻辑工具（可单测）。
 */

/**
 * 把文本切成若干片段（mock 流式打字用）。
 * 按**码点**切而不是按 UTF-16 下标 —— 否则会把 emoji / 生僻字的代理对劈开成乱码。
 */
export function chunkText(text: string, size = 8): string[] {
  const cps = Array.from(text);
  const out: string[] = [];
  for (let i = 0; i < cps.length; i += size) out.push(cps.slice(i, i + size).join(''));
  return out;
}

/** 把正文里的 `【来源N】` 转成 markdown 链接（`#cite-N`），交给渲染器变成可点击标记。 */
export function linkifyCitations(md: string): string {
  return md.replace(/【来源(\d+)】/g, '[【来源$1】](#cite-$1)');
}

/** 判断一个 href 是否是引用标记。 */
export const CITATION_HREF = /^#cite-(\d+)$/;

/** 由 `#cite-N` 取出 N。 */
export function citationNumberFromHref(href: string): number | null {
  const m = CITATION_HREF.exec(href);
  return m ? Number(m[1]) : null;
}

/** 引用按 contextIndex 匹配（兜底按数组下标）。 */
export function citationByIndex(citations: Citation[], n: number): Citation | undefined {
  return citations.find((c) => c.contextIndex === n) ?? citations[n - 1];
}

/**
 * 业务错误码 → 人话（接口清单 §3.3）。
 * `biz_1004`（引用护栏未过）是内部质量标记、`biz_1005`（能力边界）答案本身已含引导
 * —— 这两个**不打扰用户**，正常展示答案即可。
 */
const ERROR_TEXT: Record<string, string> = {
  biz_1002: '请补充更具体的公司名称或项目编号',
  biz_1003: '知识库中没有找到相关内容',
  biz_1006: '未授权：请配置访问密钥或联系管理员',
  sys_2003: '请求过于频繁，请稍后重试',
  sys_2004: '当前请求较多，请稍后重试',
  sys_2500: '服务异常，请稍后重试',
  model_3001: '模型服务暂时不可用',
  model_3002: '模型服务暂时不可用',
  model_3003: '模型服务暂时不可用',
  dep_4001: '依赖服务暂时不可用',
  dep_4002: '依赖服务暂时不可用',
};

/** 该错误码是否值得给用户显式提示。 */
export function shouldSurfaceError(code: string | null | undefined): boolean {
  if (!code) return false;
  return code !== 'biz_1004' && code !== 'biz_1005';
}

/** 错误码 → 提示文案（不值得提示的返回 null）。 */
export function errorText(code: string | null | undefined): string | null {
  if (!shouldSurfaceError(code)) return null;
  return ERROR_TEXT[code as string] ?? `请求未成功（${code}）`;
}

/* ── 会话持久化（刷新后恢复上下文） ───────────────────────── */

const SESSION_KEY = 'ztb.session_id';

export function loadSessionId(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

export function saveSessionId(id: string): void {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    /* 隐私模式等写入失败：不影响本次会话 */
  }
}

export function clearSessionId(): void {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    /* 同上 */
  }
}

/** 上传限制（与后端一致：20MB / 扩展名白名单）。前端先校验，免得白跑一趟。 */
export const MAX_UPLOAD_MB = 20;
export const ALLOWED_EXTS = [
  '.pdf', '.csv', '.txt', '.md', '.markdown',
  '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp',
];

/** 前端预校验：返回错误文案，通过则返回 null。 */
export function validateUpload(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ALLOWED_EXTS.some((e) => name.endsWith(e))) {
    return `不支持的文件类型：${file.name}（支持 PDF / 图片 / CSV / 文本）`;
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    return `文件超过 ${MAX_UPLOAD_MB} MB：${file.name}`;
  }
  return null;
}
