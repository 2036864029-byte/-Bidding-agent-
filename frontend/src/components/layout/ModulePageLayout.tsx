import type { ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { ArrowLeft, Wrench } from 'lucide-react';
import { Brand } from '@/components/common/Brand';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

/**
 * ModulePageLayout —— 模块页骨架。
 * 顶部：品牌（点击回首页）+「返回首页」按钮；中部：模块名 + 阶段徽章 + 内容区。
 * 三模块内容在 Phase 3 落地；此处提供稳定的页面容器。
 */
export function ModulePageLayout({
  name,
  eyebrow,
  description,
  children,
}: {
  name: string;
  eyebrow: string;
  description: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-[var(--color-rule)]">
        <div className="mx-auto flex w-full max-w-[72rem] items-center justify-between px-6 py-4">
          <Brand />
          <Button variant="ghost" size="sm" onClick={() => navigate('/chat')}>
            <ArrowLeft className="h-4 w-4" aria-hidden />
            返回首页
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[72rem] flex-1 px-6 py-8">
        <div className="mb-8">
          <p className="mono-label mb-2">{eyebrow}</p>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--color-ink)]">
              {name}
            </h1>
            <Badge variant="neutral">
              <Wrench className="h-3 w-3" aria-hidden />
              Phase 3 建设中
            </Badge>
          </div>
          <p className="mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--color-muted)]">
            {description}
          </p>
        </div>
        {children}
      </main>
    </div>
  );
}
