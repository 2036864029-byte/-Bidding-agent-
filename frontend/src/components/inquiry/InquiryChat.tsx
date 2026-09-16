import { useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { FileChips } from '@/components/chat/FileChips';
import { PriceRecordsTable } from '@/components/chat/PriceRecordsTable';
import { validateUpload } from '@/lib/chat';
import { cn } from '@/lib/utils';
import { api, type UploadedFileState } from '@/services/api';
import type { ChatMessage, ChatResponse } from '@/types/chat';

/**
 * InquiryChat —— 对话式需求描述区（设计文档 §4.4 左栏）。
 *
 * 走的是**真实对话链路**：`api.chat.send` → 后端 `/chat`（价格/标讯查询路由）。
 * 演示态下 `dataConfig.chat='mock'`，mock 侧按关键词路由到 `price_records` fixture；
 * Phase 4 翻成 `api` 后本组件**零改动**。
 *
 * 返回 `price_records` 时渲染历史中标记录表，并通过 `onResult` **把结果交给右栏**
 * —— 右栏的报价参考就是由这一次结果派生的（见 pages/inquiry/InquiryPage.tsx）。
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
    '描述项目概况，或上传招标文件/图片让我解析，我来找相似项目的中标价给你参考。也可以直接问某家公司近期的中标记录。',
};

export function InquiryChat({
  initialQuestion = '',
  onResult,
}: {
  initialQuestion?: string;
  /** 每次成功拿到结果时回调（右栏报价参考的数据源）；question 用于给参考卡做标题。 */
  onResult?: (res: ChatResponse, question: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  // 从对话模块联动过来时预填原问题（上下文不丢），由用户决定是否发送
  const [input, setInput] = useState(initialQuestion);
  const [pending, setPending] = useState(false);
  const [files, setFiles] = useState<UploadedFileState[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  /**
   * 本栏的会话 id。上传可能**先于**提问开出一个会话，必须采纳它——
   * 否则后续提问落到另一个会话，`file_id` 归属对不上（后端会拒）。
   */
  const sessionRef = useRef<string | null>(null);

  // 跟随最新消息滚到底（用 scrollTop 赋值而非 scrollTo：后者在 jsdom 等环境不存在）
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  const upload = (picked: File[]) => {
    // 预校验：不通过的直接提示，**不产生 chip**（免得用户对着不可用的文件提问）
    const accepted = picked.filter((f) => {
      const err = validateUpload(f);
      if (err) setNotice(err);
      return !err;
    });
    if (!accepted.length) return;
    setNotice(null);

    // 串行上传：同一客户端连传会撞限流（429），串行最省事也最稳
    void (async () => {
      for (const file of accepted) {
        const key = `f_${file.name}_${file.size}_${Date.now()}`;
        setFiles((s) => [...s, { id: key, name: file.name, status: 'pending' }]);
        try {
          await api.chat.upload(file, key, sessionRef.current, (u) => {
            if (u.sessionId && !sessionRef.current) sessionRef.current = u.sessionId;
            setFiles((s) => s.map((x) => (x.id === u.id ? u : x)));
          });
        } catch (err) {
          setFiles((s) =>
            s.map((x) =>
              x.id === key ? { ...x, status: 'error' as const, error: (err as Error).message } : x
            )
          );
        }
      }
    })();
  };

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || pending) return;

    // 只带**解析完成**的文件（解析中的 file_id 后端还没有内容）
    const fileIds = files.filter((f) => f.status === 'done' && f.fileId).map((f) => f.fileId!);

    setInput('');
    setMessages((m) => [...m, { id: uid(), role: 'user', content: q }]);
    setPending(true);

    try {
      const res = await api.chat.send(q, {
        fileIds,
        sessionId: sessionRef.current ?? undefined,
      });
      if (res.sessionId) sessionRef.current = res.sessionId;
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
      onResult?.(res, q);
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
        <p className="mono-label mb-1">项目概况</p>
        <p className="text-[13px] text-[var(--color-muted)]">
          说清楚要采购什么、什么规格，或上传招标文件/图片，我来找相似项目的中标价
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
              正在检索相似项目…
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

      <div className="space-y-2 border-t border-[var(--color-rule)] px-3.5 py-3">
        {notice && (
          <p role="alert" className="px-1 text-[12px] text-[var(--color-danger)]">
            {notice}
          </p>
        )}
        <FileChips files={files} onRemove={(id) => setFiles((s) => s.filter((f) => f.id !== id))} />
        <ChatInputRow
          value={input}
          onChange={setInput}
          onSend={() => ask(input)}
          onUpload={upload}
          busy={pending}
          placeholder="例如：采购 120 台 400 万像素网络摄像机"
        />
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const price = message.data?.kind === 'price_records' ? message.data : null;

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
        {price && price.records.length > 0 && (
          <PriceRecordsTable records={price.records} columns={price.columns} />
        )}
      </div>
    </div>
  );
}
