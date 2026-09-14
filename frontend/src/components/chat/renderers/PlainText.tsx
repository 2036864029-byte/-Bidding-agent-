import { ShieldAlert } from 'lucide-react';
import { Markdown } from '@/components/chat/Markdown';
import { Notice } from '@/components/chat/renderers/KbAnswer';
import { errorText } from '@/lib/chat';
import type { ChatMessage } from '@/types/chat';

/** PlainText —— `chat`（闲聊）与 `fallback`（降级）共用：纯文本气泡。 */
export function PlainText({ message }: { message: ChatMessage }) {
  const warn = errorText(message.errorCode);
  return (
    <div>
      <Markdown>{message.content}</Markdown>
      {warn && <Notice>{warn}</Notice>}
    </div>
  );
}

/** Safety —— 安全边界（拒答/引导）：加一条明确的边界标识，别让它看着像正常回答。 */
export function Safety({ message }: { message: ChatMessage }) {
  const warn = errorText(message.errorCode);
  return (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-[12px] font-medium text-[var(--color-warning)]">
        <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
        安全边界提示
      </p>
      <Markdown>{message.content}</Markdown>
      {warn && <Notice>{warn}</Notice>}
    </div>
  );
}
