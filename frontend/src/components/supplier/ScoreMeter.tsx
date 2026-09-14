import { cn } from '@/lib/utils';

/**
 * ScoreMeter —— 评分计量条。
 *
 * 规范要点：填充取强调色、**轨道取同色系的浅一档**（蓝底蓝），
 * 这样整条的「状态」是连贯的；不用两种不相关颜色拼。
 */
export function ScoreMeter({
  score,
  className,
  showValue = true,
  ariaLabel = '历史合作评分',
}: {
  score: number;
  className?: string;
  showValue?: boolean;
  ariaLabel?: string;
}) {
  const pct = Math.max(0, Math.min(100, score));
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <span
        className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--color-accent-soft)]"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={ariaLabel}
      >
        <span
          className="block h-full rounded-full bg-[var(--color-accent)]"
          style={{ width: `${pct}%` }}
        />
      </span>
      {showValue && (
        <span className="tnum shrink-0 text-[13px] font-semibold text-[var(--color-ink)]">
          {pct}
        </span>
      )}
    </div>
  );
}
