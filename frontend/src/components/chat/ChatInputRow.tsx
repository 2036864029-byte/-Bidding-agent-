import { Plus, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * ChatInputRow —— 上传 + 输入 + 发送 一体输入行。
 * dock 输入条与聚焦浮窗内输入共用；上传按钮为 Phase 4 预留（+ 异步上传）。
 */
export function ChatInputRow({
  value,
  onChange,
  onSend,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  placeholder: string;
}) {
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
    >
      {/* + 号：无边框，钴蓝加粗，hover 旋转 90°（上传为 Phase 4 预留） */}
      <button
        type="button"
        aria-label="上传文件（下一阶段）"
        title="上传文件 · 下一阶段接入"
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--color-accent)] transition-transform duration-[var(--dur-short)] ease-out hover:rotate-90 hover:bg-[var(--color-accent-soft)]"
      >
        <Plus className="h-5 w-5 stroke-[2.5]" aria-hidden />
      </button>
      {/* 输入框：渐变描边直接作用在输入框自身（无嵌套 span） */}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label="对话输入框"
        className="gradient-edge h-11 min-w-0 flex-1 rounded-xl bg-[var(--color-paper)] px-4 text-[14px] text-[var(--color-ink)] placeholder:text-[var(--color-neutral)]"
      />
      <Button type="submit" aria-label="发送" disabled={!value.trim()}>
        <Send className="h-4 w-4" aria-hidden />
        <span className="hidden sm:inline">发送</span>
      </Button>
    </form>
  );
}
