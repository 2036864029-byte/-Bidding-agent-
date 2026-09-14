import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

/*
 * Badge —— 状态/标签小徽章
 * Hallmark：等宽小标签注册（machine-readout），强调色只做信号、不铺面
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-[6px] px-2 py-0.5 ' +
    'font-mono text-[11px] font-medium tracking-wide leading-5 ' +
    'transition-colors duration-[var(--dur-micro)] ease-out',
  {
    variants: {
      variant: {
        // 信号徽章：近蓝浅底 + 电钴蓝字
        accent: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
        // 中性：纸面抬升 + 发丝线
        neutral: 'bg-[var(--color-paper-2)] text-[var(--color-muted)] border border-[var(--color-rule)]',
        // 语义三色
        success: 'bg-[oklch(92%_0.04_150)] text-[var(--color-success)]',
        warning: 'bg-[oklch(94%_0.05_60)] text-[oklch(55%_0.16_60)]',
        danger: 'bg-[oklch(93%_0.05_25)] text-[var(--color-danger)]',
        // 暗带徽章（深色窗口内）
        graphite: 'bg-[var(--color-graphite)] text-[var(--color-graphite-ink)] border border-[oklch(92%_0.008_255/0.15)]',
      },
    },
    defaultVariants: {
      variant: 'neutral',
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
