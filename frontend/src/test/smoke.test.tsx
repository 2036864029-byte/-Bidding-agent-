import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/* Phase 1 冒烟：验证 shadcn 组件 / 别名 / cn 工具在 Vitest 下可用。
 * Phase 2 起：适配器单测（mock → Domain 契约）与 Mock↔API 开关测试落在对应用例。 */

describe('ui 基建冒烟', () => {
  it('Button 渲染并支持 loading 态', () => {
    render(
      <Button state="loading" loadingText="处理中…">
        发送
      </Button>
    );
    expect(screen.getByText('处理中…')).toBeInTheDocument();
    expect(screen.getByRole('button')).toHaveAttribute('aria-busy', 'true');
  });

  it('Badge 渲染语义变体', () => {
    render(<Badge variant="success">就绪</Badge>);
    const badge = screen.getByText('就绪');
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain('bg-[oklch(92%_0.04_150)]');
  });
});

describe('cn 工具', () => {
  it('合并冲突类名取后者', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
    expect(cn('text-sm', undefined, 'font-medium')).toBe('text-sm font-medium');
  });
});
