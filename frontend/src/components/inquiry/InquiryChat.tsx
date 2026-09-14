import { useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { PriceRecordsTable } from '@/components/inquiry/PriceRecordsTable';
import { cn } from '@/lib/utils';
import { api } from '@/services/api';
import type { ChatMessage } from '@/types/chat';

/**
 * InquiryChat —— 对话式需求描述区（设计文档 §4.4 左栏）。
 *
 * 走的是**真实对话链路**：`api.chat.send` → 后端 `/chat`（价格/标讯查询路由）。
 * 演示态下 `dataConfig.chat='mock'`，mock 侧按关键词路由到 `price_records` fixture；
 * Phase 4 翻成 `api` 后本组件**零改动**。
 *
 * 返回 `price_records` 时渲染历史中标记录表 —— 这就是「调双库查历史中标金额给价格参考」。
 */

let seq = 0;
const uid = () => `iq_${++seq}`;

const SUGGESTIONS = [
  '高清网络摄像机近两年的中标均价大概多少？',
  '杭州海康威视最近中标了哪些项目？',
  '智慧园区安防系统的采购预算一般多少？',
];

const WELCOME: ChatMessage = {
  id: 'welcome',
  role: 'agent',
  content:
    '描述你要采购的物资或服务，我来调取历史中标数据给你价格参考。也可以直接问某家公司近期的中标记录。',
};

export function InquiryChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // 跟随最新消息滚到底（用 scrollTop 赋值而非 scrollTo：后者在 jsdom 等环境不存在）
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || pending) return;
    setInput('');
    setMessages((m) => [...m, { id: uid(), role: 'user', content: q }]);
    setPending(true);

    try {
      const res = await api.chat.send(q);
      setMessages((m) => [
        ...m,
        {
          id: uid(),
          role: 'agent',
          content: res.answer,
          replyType: res.replyType,
          citations: res.citations,
          data: res.data,
          errorCode: res.errorCode,
        },
      ]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: uid(),
          role: 'agent',
          content: `抱歉，这次没能取到数据：${(err as Error).message}`,
        },
      ]);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex h-[38rem] flex-col rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] lg:sticky lg:top-6">
      <header className="border-b border-[var(--color-rule)] px-5 py-3.5">
        <p className="mono-label mb-1">需求描述</p>
        <p className="text-[13px] text-[var(--color-muted)]">
          用自然语言说清楚要买什么、什么规格，我来查历史中标价给你参考
        </p>
      </header>

      <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}

        {pending && (
          <div className="flex justify-start" aria-live="polite">
            <div className="flex items-center gap-2 rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-2.5 text-[13px] text-[var(--color-muted)]">
              <Sparkles className="h-3.5 w-3.5 animate-pulse text-[var(--color-accent)]" aria-hidden />
              正在检索历史中标数据…
            </div>
          </div>
        )}

        {messages.length === 1 && !pending && (
          <div className="flex flex-wrap gap-2 pt-1">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => ask(s)}
                className="rounded-full border border-[var(--color-rule-2)] px-3 py-1.5 text-[12.5px] text-[var(--color-muted)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-[var(--color-rule)] px-3.5 py-3">
        <ChatInputRow
          value={input}
          onChange={setInput}
          onSend={() => ask(input)}
          placeholder="例如：采购 120 台 400 万像素网络摄像机"
        />
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const records = message.data?.kind === 'price_records' ? message.data.records : [];

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn('max-w-[92%] space-y-2.5', isUser && 'max-w-[85%]')}>
        <div
          className={cn(
            'whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed',
            isUser
              ? 'bg-[var(--color-accent)] text-[var(--color-accent-ink)]'
              : 'border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)]'
          )}
        >
          {message.content}
        </div>
        {records.length > 0 && <PriceRecordsTable records={records} />}
      </div>
    </div>
  );
}
