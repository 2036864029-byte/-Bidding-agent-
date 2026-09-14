import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Database, MessageSquare, RotateCcw, Server, X } from 'lucide-react';
import { AgentLoading } from '@/components/chat/AgentLoading';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { FileChips } from '@/components/chat/FileChips';
import { ReplyRenderer } from '@/components/chat/renderers/ReplyRenderer';
import { dataConfig, setDataSource } from '@/services/api';
import { useChatWindowStore } from '@/store/chat-window';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/types/chat';

/**
 * ChatFocusPanel —— 对话控制台（落地页聚焦浮窗，Phase 4 接真后端）。
 *
 * 消息按 `reply_type` 注册表分发渲染；生成中给打字机光标；慢路径（agent 9~15s）
 * 前几秒没有 token，由 `AgentLoading` 顶上，避免被读成卡死。
 * × / ESC / 点遮罩 = **收起（非销毁）**，会话与流式结果都保留。
 */
export function ChatFocusPanel() {
  const {
    status, messages, input, setInput, submit, stop, minimize,
    files, busy, notice, uploadFiles, removeFile, reset,
  } = useChatWindowStore();
  const listRef = useRef<HTMLDivElement>(null);

  // 跟随最新消息滚到底（scrollTop 而非 scrollTo：后者在 jsdom 等环境不存在）
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    if (status !== 'open') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') minimize();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [status, minimize]);

  if (status !== 'open') return null;

  const last = messages[messages.length - 1];
  // 慢路径占位：agent 消息在等第一个 token（内容还空着）
  const waitingFirstToken = last?.role === 'agent' && last.pending && !last.content;
  const isMock = dataConfig.chat === 'mock';

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
          {/* 浮窗头：对话状态 + 数据源 + 清空 + 收起 */}
          <div className="flex items-center justify-between gap-3 border-b border-[var(--color-rule)] px-6 py-3.5">
            <div className="flex min-w-0 items-center gap-2.5">
              <MessageSquare className="h-4 w-4 shrink-0 text-[var(--color-accent)]" aria-hidden />
              <p className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                对话
              </p>
              <span className="mono-label shrink-0 text-[10px]">{messages.length} 条消息</span>

              {/* 数据源开关：演示数据 ↔ 真实后端（唯一开关点的可视化入口） */}
              <button
                type="button"
                onClick={() => setDataSource('chat', isMock ? 'api' : 'mock')}
                title={isMock ? '当前为演示数据，点击切到真实后端' : '当前为真实后端，点击切回演示数据'}
                className={cn(
                  'flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-wide transition-colors duration-[var(--dur-micro)] ease-out',
                  isMock
                    ? 'border-[var(--color-rule-2)] text-[var(--color-neutral)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]'
                    : 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                )}
              >
                {isMock ? (
                  <>
                    <Database className="h-2.5 w-2.5" aria-hidden />
                    演示数据
                  </>
                ) : (
                  <>
                    <Server className="h-2.5 w-2.5" aria-hidden />
                    真实后端
                  </>
                )}
              </button>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={reset}
                aria-label="清空会话"
                title="清空会话（消息与会话上下文一并重置）"
                className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              </button>
              <button
                type="button"
                onClick={minimize}
                aria-label="收起对话"
                title="收起（Esc）"
                className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
          </div>

          {/* 消息列表 */}
          <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
            {messages.map((m) => (
              <MessageRow key={m.id} message={m} />
            ))}
            {waitingFirstToken && (
              <div className="flex justify-start">
                <AgentLoading />
              </div>
            )}
          </div>

          {/* 浮窗内输入 */}
          <div className="space-y-2.5 border-t border-[var(--color-rule)] px-3.5 py-3">
            {notice && (
              <p className="px-1 text-[12px] text-[var(--color-danger)]" role="alert">
                {notice}
              </p>
            )}
            <div className="px-1">
              <FileChips files={files} onRemove={removeFile} />
            </div>
            <ChatInputRow
              value={input}
              onChange={setInput}
              onSend={submit}
              onStop={stop}
              onUpload={uploadFiles}
              busy={busy}
              placeholder="追问，或输入新问题…"
            />
          </div>
        </div>
      </motion.div>
    </div>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-[var(--color-accent)] px-4 py-2.5 text-[14px] leading-relaxed text-[var(--color-accent-ink)]">
          {message.content}
        </div>
      </div>
    );
  }

  const streaming = Boolean(message.pending);

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          'w-full rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-3 text-[13.5px] text-[var(--color-ink-2)]',
          streaming && 'border-[var(--color-rule-2)]'
        )}
      >
        {message.content || !streaming ? (
          <div className={cn(streaming && 'typing-caret')}>
            <ReplyRenderer message={message} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
