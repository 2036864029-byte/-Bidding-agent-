import { useRef } from 'react';
import { Plus, Send, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ALLOWED_EXTS } from '@/lib/chat';

/**
 * ChatInputRow —— 上传 + 输入 + 提交/停止 一体输入行（V2 · 政务蓝）。
 * 首页 hero 与聚焦浮窗内共用；模块页 dock 亦复用同一条。
 *
 * - `submitLabel`：提交键文案（首页用「开始提问」，其它场景默认「发送」）
 * - 提交键 = 品牌蓝底白字（#005EFF 对白 5.19:1，过 AA）
 * - 输入框：白底 + 2px 墨边胶囊，聚焦靠全局焦点环（不再有渐变发光）
 *
 * Phase 4：`＋` 接真实文件选择（异步上传 + 轮询，见 store）；生成中提交键变**停止**。
 */
export function ChatInputRow({
  value,
  onChange,
  onSend,
  placeholder,
  submitLabel = '发送',
  onUpload,
  onStop,
  busy = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  placeholder: string;
  /** 提交键文案与可访问名；默认「发送」 */
  submitLabel?: string;
  onUpload?: (files: File[]) => void;
  onStop?: () => void;
  busy?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        // 空输入不发送，但也不把主按钮做灰 —— 聚焦输入框即可（store.submit 同样有二次兜底）
        if (!value.trim()) {
          inputRef.current?.focus();
          return;
        }
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

      {/* + 号：无边框，墨色加粗，hover 旋转 90° */}
      <button
        type="button"
        aria-label="上传文件"
        title="上传招标文件 / 图片 / 表格（异步解析）"
        onClick={() => fileRef.current?.click()}
        disabled={!onUpload}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--color-edge)] transition-transform duration-[var(--dur-short)] ease-out hover:rotate-90 hover:bg-[var(--color-paper-2)] disabled:opacity-40"
      >
        <Plus className="h-5 w-5 stroke-[2.5]" aria-hidden />
      </button>

      {/* 输入框：白底 + 墨边胶囊（无渐变描边、无外发光） */}
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label="对话输入框"
        className="h-11 min-w-0 flex-1 rounded-full border-2 border-[var(--color-edge)] bg-white px-4 text-[14px] text-[var(--color-edge)] placeholder:text-[var(--color-neutral)] focus-visible:outline-offset-2"
      />

      {busy && onStop ? (
        <Button
          type="button"
          aria-label="停止生成"
          onClick={onStop}
          className="rounded-full border-2 border-[var(--color-edge)] bg-white text-[var(--color-edge)] hover:bg-[var(--color-paper-2)]"
        >
          <Square className="h-3.5 w-3.5" aria-hidden />
          <span className="hidden sm:inline">停止</span>
        </Button>
      ) : (
        <Button
          type="submit"
          aria-label={submitLabel}
          disabled={busy}
          className="rounded-full border-2 border-[var(--color-edge)] bg-[var(--color-azure)] font-bold text-white hover:-translate-y-px hover:bg-[var(--color-azure-dp)]"
        >
          <Send className="h-4 w-4" aria-hidden />
          <span className="hidden sm:inline">{submitLabel}</span>
        </Button>
      )}
    </form>
  );
}
