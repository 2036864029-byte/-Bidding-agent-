import { ChevronRight, FileText } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Citation } from '@/types/chat';

/**
 * Citations —— 引用来源卡片（`kb_answer` 专用）。
 *
 * 正文里的【来源N】点击后展开对应卡片 —— 引用要能**核验**才有意义，
 * 只把来源列在下面、点不动，等于没有。
 */
export function Citations({
  citations,
  openIndex,
  onToggle,
}: {
  citations: Citation[];
  openIndex: number | null;
  onToggle: (n: number) => void;
}) {
  if (!citations.length) return null;

  return (
    <section className="mt-3.5">
      <p className="mono-label mb-2">引用来源 · {citations.length}</p>
      <ul className="space-y-1.5">
        {citations.map((c) => {
          const open = openIndex === c.contextIndex;
          return (
            <li
              key={c.chunkUid || c.contextIndex}
              className={cn(
                'overflow-hidden rounded-xl border transition-colors duration-[var(--dur-short)] ease-out',
                open
                  ? 'border-[var(--color-accent)] bg-[var(--color-paper)]'
                  : 'border-[var(--color-rule)] bg-[var(--color-paper-2)]'
              )}
            >
              <button
                type="button"
                onClick={() => onToggle(c.contextIndex)}
                aria-expanded={open}
                className="flex w-full items-start gap-2 px-3 py-2 text-left"
              >
                <ChevronRight
                  className={cn(
                    'mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-neutral)] transition-transform duration-[var(--dur-short)] ease-out',
                    open && 'rotate-90'
                  )}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="mono-label shrink-0 text-[var(--color-accent)]">
                      【来源{c.contextIndex}】
                    </span>
                    <span className="text-[12.5px] font-medium text-[var(--color-ink)]">
                      {c.docName}
                    </span>
                    {c.score > 0 && (
                      <span className="tnum shrink-0 text-[11px] text-[var(--color-neutral)]">
                        相关度 {c.score.toFixed(3)}
                      </span>
                    )}
                  </span>
                  {c.chapter && (
                    <span className="mt-0.5 block truncate text-[12px] text-[var(--color-muted)]">
                      {c.chapter}
                    </span>
                  )}
                </span>
              </button>

              {open && (
                <div className="border-t border-[var(--color-rule)] px-3 py-2.5">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] text-[var(--color-neutral)]">
                    <FileText className="h-3 w-3" aria-hidden />
                    原文片段（第 {c.chunkIndex} 块）
                  </p>
                  <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">
                    {c.text}
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
