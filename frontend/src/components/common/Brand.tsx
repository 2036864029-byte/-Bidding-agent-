import { Link } from 'react-router';
import { cn } from '@/lib/utils';

/**
 * 品牌区：「招小智」wordmark。
 * 显示字体 Space Grotesk + CJK 回退；Logo 为钴蓝圆角方块 + 智字。
 * 点击回首页（/chat）。
 * compact：吸顶导航变体 —— Logo 略小、隐藏 mono 副标、文字一档。
 */
export function Brand({
  className,
  onNavigate,
  compact = false,
}: {
  className?: string;
  onNavigate?: () => void;
  compact?: boolean;
}) {
  const inner = (
    <span className="flex items-center gap-2.5">
      <span
        aria-hidden
        className={cn(
          'flex items-center justify-center rounded-[10px] bg-[var(--color-accent)] font-semibold text-[var(--color-accent-ink)] shadow-[var(--shadow-lift)]',
          compact ? 'h-8 w-8 text-[13px]' : 'h-9 w-9 text-[15px]'
        )}
      >
        智
      </span>
      <span className="flex flex-col leading-none">
        <span
          className={cn(
            'font-display font-semibold tracking-[-0.02em] text-[var(--color-ink)]',
            compact ? 'text-[1.02rem]' : 'text-[1.15rem]'
          )}
        >
          招小智
        </span>
        {!compact && (
          <span className="mono-label mt-1 text-[10px]">ZT · BIDDING INTELLIGENCE</span>
        )}
      </span>
    </span>
  );

  return (
    <Link
      to="/chat"
      onClick={onNavigate}
      aria-label="招小智 · 返回首页"
      className={cn(
        'rounded-md outline-offset-4 transition-transform duration-[var(--dur-short)] ease-out',
        'hover:-translate-y-px',
        className
      )}
    >
      {inner}
    </Link>
  );
}
