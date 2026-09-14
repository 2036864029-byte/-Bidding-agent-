import { motion } from 'framer-motion';
import { MessageSquare } from 'lucide-react';
import { useChatWindowStore } from '@/store/chat-window';

/**
 * ChatResumePill —— 「对话中」胶囊（D6）。
 * 浮窗 ×/ESC/遮罩收起后出现；点击恢复同一会话（消息不丢）。
 */
export function ChatResumePill() {
  const { status, messages, open } = useChatWindowStore();
  if (status !== 'minimized' || messages.length === 0) return null;

  return (
    <motion.button
      type="button"
      onClick={open}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="inline-flex items-center gap-2 rounded-full border border-[var(--color-accent)] bg-[var(--color-paper)] px-4 py-2 text-[13px] font-medium text-[var(--color-accent)] shadow-[var(--shadow-lift)] transition-[background-color,transform] duration-[var(--dur-short)] ease-out hover:-translate-y-px hover:bg-[var(--color-accent-soft)]"
    >
      <MessageSquare className="h-3.5 w-3.5" aria-hidden />
      继续对话
      <span className="rounded-full bg-[var(--color-accent)] px-1.5 py-0.5 font-mono text-[10px] leading-none text-[var(--color-accent-ink)]">
        {messages.length}
      </span>
    </motion.button>
  );
}
