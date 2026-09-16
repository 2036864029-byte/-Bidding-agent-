import { useState } from 'react';
import { Search, Send } from 'lucide-react';
import { EmptyState } from '@/components/common/EmptyState';
import { Button } from '@/components/ui/button';
import { ProjectReferenceList } from '@/components/inquiry/ProjectReferenceList';
import { ProjectReferenceSummary } from '@/components/inquiry/ProjectReferenceSummary';
import { BackendError } from '@/services/backend';
import { api } from '@/services/api';
import type { ReferenceOverview } from '@/types/inquiry';

/**
 * ProjectReferencePanel —— 「项目概况参考」独立链路（POST /inquiry/reference）。
 *
 * 与左栏对话派生（`buildReference`，元口径）**并行**：这里粘贴/输入一段招标项目
 * 概况（≥10 字）→ 语义检索历史中标公告 → 返回相似项目 + 万元金额区间 + 原文链接
 * + 产品明细。不进入聊天路由，输入即查，状态全在本组件内。
 */

/** 与后端契约一致的输入下限（方案 §6）。 */
const MIN_CHARS = 10;

/** 错误 → 人话（优先后端 detail，网络/过载给兜底文案）。 */
function describeFetchError(err: unknown): string {
  if (err instanceof BackendError) {
    if (err.status === 400) return err.message || '请提供更完整的项目概况（至少 10 字）';
    if (err.status === 503)
      return err.message || '项目概况知识库暂不可用，请稍后重试';
    if (err.status === 0) return '网络请求失败，请确认后端服务已启动';
    return err.message || '查询失败，请稍后重试';
  }
  return '查询失败，请稍后重试';
}

export function ProjectReferencePanel() {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReferenceOverview | null>(null);

  const trimmed = text.trim();
  const canSubmit = trimmed.length >= MIN_CHARS && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.reference.fetch(trimmed, { topK: 10 });
      setResult(res);
    } catch (err) {
      setError(describeFetchError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5">
      <header className="mb-3 flex items-center gap-1.5">
        <Search className="h-3.5 w-3.5 text-[var(--color-neutral)]" aria-hidden />
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
          项目概况参考
        </h2>
        <span className="text-[12px] text-[var(--color-neutral)]">
          （粘贴项目概况，检索历史相似中标）
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
        rows={3}
        disabled={busy}
        className="w-full resize-y rounded-xl border border-[var(--color-rule-2)] bg-[var(--color-paper-2)] px-3.5 py-3 text-[13.5px] leading-relaxed text-[var(--color-ink)] placeholder:text-[var(--color-neutral)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)] disabled:opacity-60"
      />

      <div className="mt-2.5 flex items-center justify-between gap-3">
        <p className="text-[12px] text-[var(--color-neutral)]">
          {trimmed.length < MIN_CHARS
            ? `至少 ${MIN_CHARS} 字（当前 ${trimmed.length}）`
            : `已输入 ${trimmed.length} 字`}
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
          !error && (
            <p className="text-[12.5px] leading-relaxed text-[var(--color-muted)]">
              查询后这里会显示相似中标项目的金额区间（万元）、原文链接与产品明细。
            </p>
          )
        )}
      </div>
    </section>
  );
}
