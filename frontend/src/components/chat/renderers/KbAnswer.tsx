import { useState, type ReactNode } from 'react';
import { Citations } from '@/components/chat/Citations';
import { Markdown } from '@/components/chat/Markdown';
import { errorText } from '@/lib/chat';
import type { ChatMessage } from '@/types/chat';

/**
 * KbAnswer —— 知识问答（`kb_answer`）：正文 + 可展开引用卡片。
 * 正文里的【来源N】点击即展开对应来源 —— 引用要能核验。
 */
export function KbAnswer({ message }: { message: ChatMessage }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const citations = message.citations ?? [];
  const warn = errorText(message.errorCode);

  return (
    <div>
      <Markdown onCitationClick={(n) => setOpenIndex((cur) => (cur === n ? null : n))}>
        {message.content}
      </Markdown>
      {warn && <Notice>{warn}</Notice>}
      <Citations
        citations={citations}
        openIndex={openIndex}
        onToggle={(n) => setOpenIndex((cur) => (cur === n ? null : n))}
      />
    </div>
  );
}

/** 轻量提示条（业务错误码文案等）。 */
export function Notice({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 rounded-lg border border-[var(--color-rule-2)] bg-[var(--color-paper-2)] px-3 py-2 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
      {children}
    </p>
  );
}
