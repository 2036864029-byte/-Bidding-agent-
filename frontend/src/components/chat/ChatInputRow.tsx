import { useRef } from 'react';
import { Plus, Send, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ALLOWED_EXTS } from '@/lib/chat';

/**
 * ChatInputRow —— 上传 + 输入 + 发送/停止 一体输入行。
 * dock 输入条与聚焦浮窗内输入共用。
 *
 * Phase 4：`＋` 接真实文件选择（异步上传 + 轮询，见 store）；生成中发送键变**停止**。
 */
export function ChatInputRow({
  value,
  onChange,
  onSend,
  placeholder,
  onUpload,
  onStop,
  busy = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  placeholder: string;
  onUpload?: (files: File[]) => void;
  onStop?: () => void;
  busy?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
    >
      <input
        ref={fileRef}
        type="file"
        multiple
        accept={ALLOWED_EXTS.join(',')}
        className="sr-only"
        onChange={(e) => {
          const picked = Array.from(e.target.files ?? []);
          if (picked.length) onUpload?.(picked);
          e.target.value = ''; // 允许重复选同一个文件
        }}
      />

      {/* + 号：无边框，钴蓝加粗，hover 旋转 90° */}
      <button
        type="button"
        aria-label="上传文件"
        title="上传招标文件 / 图片 / 表格（异步解析）"
        onClick={() => fileRef.current?.click()}
        disabled={!onUpload}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--color-accent)] transition-transform duration-[var(--dur-short)] ease-out hover:rotate-90 hover:bg-[var(--color-accent-soft)] disabled:opacity-40"
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

      {busy && onStop ? (
        <Button type="button" variant="secondary" aria-label="停止生成" onClick={onStop}>
          <Square className="h-3.5 w-3.5" aria-hidden />
          <span className="hidden sm:inline">停止</span>
        </Button>
      ) : (
        <Button type="submit" aria-label="发送" disabled={!value.trim() || busy}>
          <Send className="h-4 w-4" aria-hidden />
          <span className="hidden sm:inline">发送</span>
        </Button>
      )}
    </form>
  );
}
