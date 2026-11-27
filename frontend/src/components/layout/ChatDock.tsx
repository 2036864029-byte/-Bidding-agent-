import { useChatWindowStore } from '@/store/chat-window';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { ChatResumePill } from '@/components/chat/ChatResumePill';
import { cn } from '@/lib/utils';

/**
 * ChatDock —— 对话框（D5/D7 · V2 政务蓝）。
 *
 * 三个变体共用同一份实现，保证「每个界面下方都有同一条对话框」：
 * - `inline`：随文档流排在所在屏的底部（首页首屏、三模块屏各一处）
 * - `sticky`：吸底常驻（三个模块详情页）
 *
 * `submitLabel`：首页/三模块屏用「开始提问」，模块详情页沿用「发送」。
 * 三处输入框绑定同一个 store —— 一次会话跨界面不丢；浮窗打开时全部让位。
 *
 * `inline` 的内边距是 `pt-6 pb-8`：上面留 24px 与分隔线拉开距离（原先只有 pb，
 * 输入行紧贴分隔线，看起来像"平齐"），下面 32px 收口。
 */
export function ChatDock({
  variant = 'inline',
  submitLabel,
}: {
  variant?: 'inline' | 'sticky';
  /** 提交键文案；不传则由 ChatInputRow 用默认「发送」 */
  submitLabel?: string;
}) {
  const { status, messages, input, setInput, submit, uploadFiles, busy } = useChatWindowStore();

  if (status === 'open') return null; // 聚焦浮窗持输入

  const sticky = variant === 'sticky';

  return (
    <section
      className={cn(
        sticky && 'sticky bottom-0 z-40 border-t-2 border-[var(--color-edge)] bg-[var(--color-paper)]/95 backdrop-blur-md'
      )}
    >
      <div className={cn('mx-auto w-full max-w-[72rem] px-6', sticky ? 'py-3' : 'pt-6 pb-8')}>
        <ChatInputRow
          value={input}
          onChange={setInput}
          onSend={submit}
          onUpload={uploadFiles}
          busy={busy}
          submitLabel={submitLabel}
          placeholder="输入你的问题，例如：招标方式有哪些？"
        />
        {status === 'minimized' && messages.length > 0 && (
          <div className="mt-4 flex justify-center">
            <ChatResumePill />
          </div>
        )}
      </div>
    </section>
  );
}
