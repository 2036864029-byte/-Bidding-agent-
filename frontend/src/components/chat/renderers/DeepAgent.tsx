import { useState } from 'react';
import { CheckCircle2, ChevronRight, CircleSlash, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Markdown } from '@/components/chat/Markdown';
import { Notice } from '@/components/chat/renderers/KbAnswer';
import { errorText } from '@/lib/chat';
import { cn } from '@/lib/utils';
import type { ChatMessage, TraceStep } from '@/types/chat';

/**
 * DeepAgent —— 多步检索（`deep_agent`）：正文 + **思维链折叠面板**。
 *
 * ⚠️ 只有这一种 reply_type 有真 trace（设计文档 §1 修正 3）——别的分支不要默认展开个空壳。
 *
 * 面板给的是**可核验的过程**：规划了几步、哪一步空了、每步耗时、每步检索到什么来源。
 * 这既是"为什么这次慢"的答案，也是"结论从哪来"的凭据。
 */
export function DeepAgent({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const data = message.data;
  const warn = errorText(message.errorCode);

  if (data?.kind !== 'deep_agent') {
    return <Markdown>{message.content}</Markdown>;
  }
  const { trace } = data;

  return (
    <div>
      <Markdown>{message.content}</Markdown>
      {warn && <Notice>{warn}</Notice>}

      <section className="mt-3.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className={cn(
            'flex w-full items-center gap-2 rounded-xl border px-3 py-2 text-left transition-colors duration-[var(--dur-short)] ease-out',
            open
              ? 'border-[var(--color-accent)] bg-[var(--color-paper)]'
              : 'border-[var(--color-rule)] bg-[var(--color-paper-2)] hover:border-[var(--color-rule-2)]'
          )}
        >
          <ChevronRight
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-[var(--color-neutral)] transition-transform duration-[var(--dur-short)] ease-out',
              open && 'rotate-90'
            )}
            aria-hidden
          />
          <span className="text-[12.5px] font-medium text-[var(--color-ink)]">思考过程</span>
          <span className="mono-label">
            {trace.plan.length} 步规划 · {trace.steps.length} 步执行
            {trace.rounds > 1 && ` · ${trace.rounds} 轮`}
          </span>
        </button>

        {open && (
          <div className="mt-2 space-y-3 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3.5 py-3">
            {trace.plan.length > 0 && (
              <div>
                <p className="mono-label mb-1.5">规划</p>
                <ol className="space-y-1">
                  {trace.plan.map((p) => (
                    <li key={p.id} className="text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">
                      <span className="tnum mr-1.5 text-[var(--color-neutral)]">{p.id}.</span>
                      {p.objective}
                      <span className="ml-1.5 font-mono text-[11px] text-[var(--color-neutral)]">
                        {p.tool}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <div>
              <p className="mono-label mb-1.5">执行</p>
              <ol className="space-y-2">
                {trace.steps.map((s) => (
                  <StepRow key={s.stepId} step={s} />
                ))}
              </ol>
            </div>

            {trace.reflection && (
              <div>
                <p className="mono-label mb-1.5">反思</p>
                <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">
                  <Badge variant={trace.reflection.verdict === 'gap' ? 'warning' : 'success'}>
                    {VERDICT_TEXT[trace.reflection.verdict] ?? trace.reflection.verdict}
                  </Badge>
                  <span className="ml-2">{trace.reflection.notes}</span>
                </p>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

const VERDICT_TEXT: Record<string, string> = {
  adequate: '结论充分',
  revised: '已修订',
  gap: '存在缺口',
};

const STATUS_META: Record<
  TraceStep['status'],
  { label: string; icon: typeof CheckCircle2; className: string }
> = {
  ok: { label: '命中', icon: CheckCircle2, className: 'text-[var(--color-success)]' },
  empty: { label: '无结果', icon: CircleSlash, className: 'text-[var(--color-warning)]' },
  error: { label: '出错', icon: XCircle, className: 'text-[var(--color-danger)]' },
};

function StepRow({ step }: { step: TraceStep }) {
  const meta = STATUS_META[step.status] ?? STATUS_META.ok;
  const Icon = meta.icon;

  return (
    <li className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Icon className={cn('h-3.5 w-3.5 shrink-0', meta.className)} aria-hidden />
        <span className={cn('text-[12px] font-medium', meta.className)}>{meta.label}</span>
        <span className="font-mono text-[11px] text-[var(--color-neutral)]">{step.tool}</span>
        <span className="tnum text-[11px] text-[var(--color-neutral)]">
          {step.recordsCount} 条
          {step.latencyMs !== null && ` · ${step.latencyMs}ms`}
        </span>
      </p>
      {step.query && (
        <p className="mt-1 truncate text-[12px] text-[var(--color-muted)]">查询：{step.query}</p>
      )}
      {step.observationHead && (
        <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-[var(--color-ink-2)]">
          {step.observationHead}
        </p>
      )}
      {step.sources.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {step.sources.map((src, i) => (
            <li key={i} className="truncate text-[11px] text-[var(--color-neutral)]">
              来源：{src}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
