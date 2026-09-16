import { useChatWindowStore } from '@/store/chat-window';
import { Markdown } from '@/components/chat/Markdown';
import type { ChatMessage } from '@/types/chat';

/**
 * Clarify —— 澄清式反问（`clarify`，Phase 3）：追问无法消解时，给出引导文案 +
 * 一键候选 chips。点击候选即以该文本发起**新一轮提问**（复用流式链路），
 * 而不是死磕一段答不出的问题。
 */
export function Clarify({ message }: { message: ChatMessage }) {
  const candidates =
    message.data?.kind === 'clarify' ? message.data.candidates : [];
  const submitText = useChatWindowStore((s) => s.submitText);

  return (
    <div>
      <Markdown>{message.content}</Markdown>
      {candidates.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {candidates.map((c) => (
            <li key={c}>
              <button
                type="button"
                onClick={() => submitText(c)}
                className="rounded-full border border-[var(--color-rule-2)] bg-[var(--color-paper-2)] px-3 py-1.5 text-[12.5px] leading-tight text-[var(--color-ink-2)] transition-colors hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
              >
                {c}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
