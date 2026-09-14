import { useEffect } from 'react';
import { motion } from 'framer-motion';
import { MessageSquare, X } from 'lucide-react';
import { useChatWindowStore } from '@/store/chat-window';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { cn } from '@/lib/utils';

/**
 * ChatFocusPanel —— 聚焦浮窗（D6）。
 * 背景虚化 + 压暗遮罩；× / ESC / 点击遮罩 = 收起（非销毁，会话保留）。
 * 输入随会话在浮窗内；max-h 70vh 内滚；max-w 与版心一致 72rem。
 * Phase 4 接真后端后，agent 消息改由 reply_type 渲染（设计文档 §4.2）。
 */
export function ChatFocusPanel() {
  const { status, messages, input, setInput, submit, minimize } = useChatWindowStore();

  // ESC = 收起（非销毁）
  useEffect(() => {
    if (status !== 'open') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') minimize();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [status, minimize]);

  if (status !== 'open') return null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center px-4">
      {/* 遮罩：点击 = 收起 */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.24, ease: 'easeOut' }}
        onClick={minimize}
        aria-hidden
        className="absolute inset-0 bg-[oklch(24%_0.02_258/0.22)] backdrop-blur-sm"
      />

      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="招小智对话浮窗"
        initial={{ opacity: 0, y: 14, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
        className="relative z-10 w-full max-w-[72rem]"
      >
        {/* 渐变描边直接作用于对话框本身（无外层框架） */}
        <div className="gradient-edge flex max-h-[70vh] flex-col rounded-[var(--radius-xl)]">
          {/* 浮窗头：对话状态 + 收起 */}
          <div className="flex items-center justify-between border-b border-[var(--color-rule)] px-6 py-4">
            <div className="flex items-center gap-2.5">
              <MessageSquare className="h-4 w-4 text-[var(--color-accent)]" aria-hidden />
              <p className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                对话
              </p>
              <span className="mono-label text-[10px]">{messages.length} 条消息</span>
            </div>
            <button
              type="button"
              onClick={minimize}
              aria-label="收起对话"
              title="收起（Esc）"
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-neutral)] transition-[background-color,color] duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          {/* 消息列表 */}
          <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {messages.map((m) => (
              <div
                key={m.id}
                className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}
              >
                <div
                  className={cn(
                    'max-w-[85%] rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed',
                    m.role === 'user'
                      ? 'bg-[var(--color-accent)] text-[var(--color-accent-ink)]'
                      : 'border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)]'
                  )}
                >
                  {m.content}
                </div>
              </div>
            ))}
          </div>

          {/* 浮窗内输入 */}
          <div className="border-t border-[var(--color-rule)] px-3.5 py-3">
            <ChatInputRow
              value={input}
              onChange={setInput}
              onSend={submit}
              placeholder="追问，或输入新问题…"
            />
          </div>
        </div>
      </motion.div>
    </div>
  );
}
