import { useEffect } from 'react';
import { SiteHeader } from '@/components/layout/SiteHeader';
import { ModulesBoard } from '@/components/layout/ModulesBoard';
import { ChatDock } from '@/components/layout/ChatDock';
import { ChatFocusPanel } from '@/components/chat/ChatFocusPanel';

/**
 * ModulesPage —— 三模块独立路由（`/modules`）。
 *
 * 与首页的关系：首页 `/chat` 只保留首屏（标题 + 对话示例卡 + 对话框），
 * 「开始」/ 导航把它带到这一页。改成独立路由后：
 *   - 浏览器后退可回到首屏
 *   - `/modules` 可直链分享、可刷新
 *   - 本页自带同一条对话框（与首页、模块详情页共用 `ChatDock` + 同一个 chat store）
 */
export function ModulesPage() {
  // 客户端路由切换不会重载文档，滚动位置会从上一页带过来 —— 进页手动归零。
  // 用 scrollTop 赋值而非 window.scrollTo：jsdom 里后者会抛 "Not implemented"。
  useEffect(() => {
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, []);

  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      <main className="flex flex-1 flex-col">
        <ModulesBoard />
      </main>
      <div className="border-t border-[var(--color-rule)]">
        <ChatDock variant="inline" submitLabel="开始提问" />
      </div>
      <ChatFocusPanel />
    </div>
  );
}
