import type { ComponentType } from 'react';
import { useNavigate } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { Brand } from '@/components/common/Brand';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * PlaceholderPage —— 导航占位页脚手架（D2）。
 * 顶部：品牌 + 返回首页；中部：EmptyState（标题 + 说明 + 建设中）。
 * 内容后续填充，路由先占好；页面内部改动不影响其余部分。
 */
export function PlaceholderPage({
  icon: Icon,
  title,
  description,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
}) {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-[var(--color-rule)] bg-[var(--color-paper)]/80 backdrop-blur-md">
        <div className="mx-auto flex w-full max-w-[72rem] items-center justify-between px-6 py-4">
          <Brand />
          <Button variant="ghost" size="sm" onClick={() => navigate('/chat')}>
            <ArrowLeft className="h-4 w-4" aria-hidden />
            返回首页
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[72rem] flex-1 px-6 py-8">
        <EmptyState
          icon={Icon}
          title={title}
          description={`${description} 内容建设中，后续将在此填充。`}
        />
      </main>
    </div>
  );
}
