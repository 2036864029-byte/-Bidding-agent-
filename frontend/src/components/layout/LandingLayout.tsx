import { SiteHeader } from '@/components/layout/SiteHeader';
import { IntroBand } from '@/components/layout/IntroBand';
import { ModuleGrid } from '@/components/layout/ModuleGrid';
import { ChatDock } from '@/components/layout/ChatDock';
import { ChatFocusPanel } from '@/components/chat/ChatFocusPanel';

/**
 * LandingLayout —— 落地页（首页 = /chat）四层结构（D1~D7）。
 *  吸顶导航（毛玻璃 + 4 占位页）→ 浅蓝介绍带 → 三模块增强卡 → 渐变输入条
 *  → 发送后聚焦浮窗（× 收起、会话可恢复）。
 * 执行方案：docs/plan1/主页面优化执行方案_20260914.md
 */
export function LandingLayout() {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      <IntroBand />
      <main className="mx-auto flex w-full max-w-[72rem] flex-1 flex-col justify-center px-6 py-4">
        <ModuleGrid />
      </main>
      <ChatDock />
      <ChatFocusPanel />
    </div>
  );
}
