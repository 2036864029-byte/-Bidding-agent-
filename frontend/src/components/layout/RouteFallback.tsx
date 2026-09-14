import { Loader2 } from 'lucide-react';

/**
 * RouteFallback —— 路由懒加载时的过渡态。
 *
 * 刻意保持**极轻**：它出现在首屏/切页瞬间，本身不能成为新的加载负担。
 * 只放一个居中的 spinner，不做骨架屏（骨架反而会先画一版再被替换，造成跳动）。
 */
export function RouteFallback() {
  return (
    <div
      className="flex min-h-dvh items-center justify-center"
      role="status"
      aria-live="polite"
    >
      <span className="flex items-center gap-2 text-[13px] text-[var(--color-neutral)]">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        加载中…
      </span>
    </div>
  );
}
