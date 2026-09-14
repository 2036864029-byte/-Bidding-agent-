import type { ComponentType, ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * EmptyState —— 空态占位。
 * Phase 3 起三模块用它承载「渐变光晕空态 + 一键注入 Mock 数据」；
 * 当前用于模块页占位。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-rule-2)]',
        'bg-[var(--color-paper)] px-8 py-16 text-center',
        className
      )}
    >
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
        <Icon className="h-6 w-6" aria-hidden />
      </span>
      <h2 className="mt-5 text-lg font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
        {title}
      </h2>
      <p className="mt-2 max-w-sm text-[14px] leading-relaxed text-[var(--color-muted)]">
        {description}
      </p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
