import { useRef, useState } from 'react';
import { FileText, Plus, Search, Send } from 'lucide-react';
import { EmptyState } from '@/components/common/EmptyState';
import { Button } from '@/components/ui/button';
import { FileChips } from '@/components/chat/FileChips';
import { ProjectReferenceList } from '@/components/inquiry/ProjectReferenceList';
import { ProjectReferenceSummary } from '@/components/inquiry/ProjectReferenceSummary';
import { validateUpload } from '@/lib/chat';
import { BackendError } from '@/services/backend';
import { api, type UploadedFileState } from '@/services/api';
import type { ReferenceOverview } from '@/types/inquiry';

/**
 * ProjectReferencePanel —— 「项目概况参考」主链路（POST /inquiry/reference）。
 *
 * 粘贴/输入一段招标项目概况（≥10 字），或**上传招标文件/图片**（复用 /files/upload
 * 解析链路，与主对话框同一套）→ 语义检索历史中标公告 → 返回相似项目 + 万元金额
 * 区间 + 原文链接 + 产品明细。不进入聊天路由，输入即查，状态全在本组件内。
 *
 * 2026-09-16 起是询价页**唯一**链路。上传的文件解析文本由服务端并入概况查询——
 * 同一份文件既能问答也能做项目概况参考。
 *
 * ⚠️ 本链路只做「项目概况 → 相似项目」：若输入明显是**供应商/中标查询**（如
 * 「海康威视中标了哪些项目」），语义检索答不了，直接引导移步「供应商全景」模块。
 */

/** 与后端契约一致的输入下限（方案 §6）。 */
const MIN_CHARS = 10;

/**
 * 供应商/中标类提问特征（不是项目概况）：这类问题语义检索答不了，
 * 应引导用户去「供应商全景」模块（那里按公司名/地区/行业筛选中标业绩）。
 */
const SUPPLIER_QUERY_RE =
  /(中标了|中标哪些|中标什么|中标历史|中标情况|中标项目|中标记录)|(推荐.{0,8}(供应商|公司))|(哪些(公司|供应商))|(有没有.{0,6}(供应商|公司))/;

/** 错误 → 人话（优先后端 detail，网络/过载给兜底文案）。 */
function describeFetchError(err: unknown): string {
  if (err instanceof BackendError) {
    if (err.status === 400) return err.message || '请提供更完整的项目概况（至少 10 字）';
    if (err.status === 404) return err.message || '上传的文件已过期或不可用，请重新上传';
    if (err.status === 503)
      return err.message || '项目概况知识库暂不可用，请稍后重试';
    if (err.status === 0) return '网络请求失败，请确认后端服务已启动';
    return err.message || '查询失败，请稍后重试';
  }
  return '查询失败，请稍后重试';
}

let seq = 0;
const uid = () => `ref_${++seq}`;

export function ProjectReferencePanel({ initialText = '' }: { initialText?: string }) {
  // 对话模块联动时把原问题预填进来（用户确认后再点查询）
  const [text, setText] = useState(initialText);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReferenceOverview | null>(null);

  // 上传（招标文件/图片）：解析完成后文件文本由后端并入概况查询
  const [files, setFiles] = useState<UploadedFileState[]>([]);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);

  // 供应商/中标类提问 → 移步引导（不提交语义检索）
  const [supplierNotice, setSupplierNotice] = useState<string | null>(null);

  const trimmed = text.trim();
  const readyFiles = files.filter((f) => f.status === 'done' && f.fileId);
  // 纯文本需 ≥10 字；有就绪文件时文本可为空（文件解析文本并入概况查询）
  const canSubmit = (trimmed.length >= MIN_CHARS || readyFiles.length > 0) && !busy;

  const upload = (picked: File[]) => {
    const accepted = picked.filter((f) => {
      const err = validateUpload(f);
      if (err) setUploadNotice(err);
      return !err;
    });
    if (!accepted.length) return;
    setUploadNotice(null);

    // 串行上传：同一客户端连传会撞限流（429），串行最省事也最稳
    void (async () => {
      for (const file of accepted) {
        const key = `${uid()}_${file.name}_${file.size}`;
        setFiles((s) => [...s, { id: key, name: file.name, status: 'pending' }]);
        try {
          await api.chat.upload(file, key, sessionRef.current, (u) => {
            if (u.sessionId && !sessionRef.current) sessionRef.current = u.sessionId;
            setFiles((s) => s.map((x) => (x.id === u.id ? u : x)));
          });
        } catch (err) {
          setFiles((s) =>
            s.map((x) =>
              x.id === key ? { ...x, status: 'error' as const, error: (err as Error).message } : x
            )
          );
        }
      }
    })();
  };

  const submit = async () => {
    if (!canSubmit) return;
    // 供应商/中标类提问：语义检索答不了，引导去供应商全景模块
    if (SUPPLIER_QUERY_RE.test(trimmed)) {
      setSupplierNotice(
        '这是供应商 / 中标项目查询，语义检索答不了——请前往「供应商全景」模块，' +
          '可按公司名、地区、行业查看工商信息与中标业绩。'
      );
      return;
    }
    setSupplierNotice(null);
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.reference.fetch(trimmed, {
        topK: 10,
        fileIds: readyFiles.map((f) => f.fileId!),
        sessionId: sessionRef.current ?? undefined,
      });
      setResult(res);
    } catch (err) {
      setError(describeFetchError(err));
    } finally {
      setBusy(false);
    }
  };

  const readyCount = files.filter((f) => f.status === 'done').length;

  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5">
      <header className="mb-3 flex items-center gap-1.5">
        <Search className="h-3.5 w-3.5 text-[var(--color-neutral)]" aria-hidden />
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
          项目概况参考
        </h2>
        <span className="text-[12px] text-[var(--color-neutral)]">
          （粘贴项目概况，或上传招标文件/图片）
        </span>
      </header>

      <label htmlFor="project-overview-input" className="sr-only">
        项目概况文本
      </label>
      <textarea
        id="project-overview-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="例：采购120台监控摄像头用于校园安防监控项目，含视频存储服务器……"
        rows={4}
        disabled={busy}
        className="w-full resize-y rounded-xl border border-[var(--color-rule-2)] bg-[var(--color-paper-2)] px-3.5 py-3 text-[13.5px] leading-relaxed text-[var(--color-ink)] placeholder:text-[var(--color-neutral)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)] disabled:opacity-60"
      />

      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label
            className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-[var(--color-rule-2)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--color-muted)] transition-colors duration-[var(--dur-short)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
            title="上传招标文件/图片（PDF、Word、图片，解析后并入概况查询）"
          >
            <Plus className="h-3 w-3" aria-hidden />
            上传文件/图片
            <input
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"
              className="sr-only"
              onChange={(e) => {
                upload(Array.from(e.target.files ?? []));
                e.target.value = '';
              }}
            />
          </label>
          {uploadNotice && (
            <p role="alert" className="text-[12px] text-[var(--color-danger)]">
              {uploadNotice}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <p className="text-[12px] text-[var(--color-neutral)]">
            {trimmed.length < MIN_CHARS && readyFiles.length === 0
              ? `至少 ${MIN_CHARS} 字（当前 ${trimmed.length}）`
              : `已输入 ${trimmed.length} 字`}
            {readyCount > 0 && ` · ${readyCount} 个文件就绪`}
          </p>
          <Button
            size="sm"
            onClick={submit}
            disabled={!canSubmit}
            state={busy ? 'loading' : undefined}
            loadingText="检索中…"
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
            查金额参考
          </Button>
        </div>
      </div>

      <div className="mt-2.5">
        <FileChips files={files} onRemove={(id) => setFiles((s) => s.filter((f) => f.id !== id))} />
      </div>

      {supplierNotice && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-3 py-2 text-[12.5px] leading-relaxed text-[var(--color-ink)]">
          <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
          {supplierNotice}
        </p>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5 px-3 py-2 text-[12.5px] text-[var(--color-danger)]">
          {error}
        </p>
      )}

      <div className="mt-4 space-y-3">
        {result ? (
          result.total > 0 ? (
            <>
              <ProjectReferenceSummary stats={result.stats} />
              <ProjectReferenceList projects={result.projects} />
            </>
          ) : (
            <EmptyState
              icon={Search}
              title="没有找到相似项目"
              description="换个更具体的项目概况试试（含产品名称 / 采购内容更容易命中），或放宽金额条件。"
              className="py-8"
            />
          )
        ) : (
          !busy &&
          !error &&
          !supplierNotice && (
            <p className="text-[12.5px] leading-relaxed text-[var(--color-muted)]">
              查询后这里会显示相似中标项目的金额区间（万元）、原文链接与产品明细。
            </p>
          )
        )}
      </div>
    </section>
  );
}
