import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

/*
 * Button —— shadcn 结构 + Hallmark 八态纪律
 * 八态：default · hover · focus-visible · active · disabled · loading · error · success
 * 动效：只动 transform/opacity；press 用 translateY(1px)（100ms in / 150ms out）
 * 焦点环即时出现，永不淡入；loading/error/success 以 data-state 驱动
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md ' +
    'font-body text-sm font-medium transition-[background-color,transform,color,border-color] ' +
    'duration-[var(--dur-short)] ease-out select-none ' +
    'disabled:pointer-events-none disabled:opacity-45 ' +
    '[&_svg]:pointer-events-none [&_svg]:shrink-0 ' +
    'active:translate-y-px',
  {
    variants: {
      variant: {
        // 信号主按钮 —— 唯一大面积强调（<5% 视口）
        default:
          'bg-[var(--color-accent)] text-[var(--color-accent-ink)] ' +
          'hover:bg-[oklch(55%_0.20_256)] hover:-translate-y-px ' +
          'data-[state=loading]:cursor-wait',
        // 次级：纸面 + 发丝线
        secondary:
          'bg-[var(--color-paper)] text-[var(--color-ink)] border border-[var(--color-rule-2)] ' +
          'hover:bg-[var(--color-paper-2)] hover:-translate-y-px',
        // 幽灵：无边框，hover 浅底
        ghost:
          'bg-transparent text-[var(--color-muted)] ' +
          'hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]',
        // 描边：信号描边
        outline:
          'bg-transparent text-[var(--color-accent)] border border-[var(--color-accent)] ' +
          'hover:bg-[var(--color-accent-soft)]',
        // 危险：语义红
        destructive:
          'bg-[var(--color-danger)] text-white ' +
          'hover:bg-[oklch(52%_0.21_25)]',
        // 链接
        link:
          'bg-transparent text-[var(--color-accent)] underline-offset-4 ' +
          'hover:underline',
      },
      size: {
        sm: 'h-8 px-3 text-[13px]',
        md: 'h-10 px-4',
        lg: 'h-12 px-6 text-base',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'md',
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /** 8 态之一：loading / error / success */
  state?: 'loading' | 'error' | 'success';
  loadingText?: string;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, state, loadingText, children, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';

    if (asChild && state) {
      // asChild 模式下不注入 data-state（Slot 透传给子元素）
      return (
        <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props}>
          {children}
        </Comp>
      );
    }

    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        data-state={state}
        aria-busy={state === 'loading'}
        {...props}
      >
        {state === 'loading' && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
        {state === 'loading' && loadingText ? loadingText : children}
      </Comp>
    );
  }
);
Button.displayName = 'Button';

export { Button, buttonVariants };
