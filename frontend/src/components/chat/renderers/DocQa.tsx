import { AlertTriangle, FileText } from 'lucide-react';
import { Markdown } from '@/components/chat/Markdown';
import { Notice } from '@/components/chat/renderers/KbAnswer';
import { errorText } from '@/lib/chat';
import type { ChatMessage } from '@/types/chat';

/**
 * DocQa —— 文档问答（`doc_qa`）：正文 + 来源列表 + **显式兜底提示**。
 *
 * `retrieval.notes` 是后端给的显式提示（相关度不足 / 内容被截断），
 * 设计文档 §4.2 要求**直接展示给用户** —— 这类"这次答得没那么可靠"的信息
 * 藏起来才是真正的问题。
 */
export function DocQa({ message }: { message: ChatMessage }) {
  const data = message.data;
  if (data?.kind !== 'doc_qa') {
    return <Markdown>{message.content}</Markdown>;
  }

  const warn = errorText(message.errorCode);

  return (
    <div>
      <Markdown>{message.content}</Markdown>

      {warn && <Notice>{warn}</Notice>}

      {data.retrieval && data.retrieval.notes.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {data.retrieval.notes.map((n, i) => (
            <li
              key={i}
              className="flex items-start gap-2 rounded-lg border border-[oklch(88%_0.06_60)] bg-[oklch(97%_0.03_60)] px-3 py-2 text-[12.5px] leading-relaxed text-[oklch(45%_0.12_60)]"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      )}

      {data.sources.length > 0 && (
        <section className="mt-3.5">
          <p className="mono-label mb-2">
            检索到的段落 · {data.sources.length}
            {data.mode === 'fulltext' && ' · 全文模式'}
          </p>
          <ul className="space-y-1.5">
            {data.sources.map((s, i) => (
              <li
                key={`${s.chunkIndex}-${i}`}
                className="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2"
              >
                <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <FileText className="h-3 w-3 shrink-0 text-[var(--color-neutral)]" aria-hidden />
                  <span className="text-[12.5px] font-medium text-[var(--color-ink)]">{s.doc}</span>
                  {s.score > 0 && (
                    <span className="tnum text-[11px] text-[var(--color-neutral)]">
                      相关度 {s.score.toFixed(3)}
                    </span>
                  )}
                </p>
                {s.chapter && (
                  <p className="mt-0.5 text-[12px] text-[var(--color-muted)]">{s.chapter}</p>
                )}
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">
                  {s.contentSnippet}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.attachedDocs.length > 0 && (
        <p className="mt-2.5 text-[12px] text-[var(--color-neutral)]">
          依据文件：{data.attachedDocs.join('、')}
        </p>
      )}
    </div>
  );
}
