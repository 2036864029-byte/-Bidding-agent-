import { useEffect } from 'react';
import { SiteHeader } from '@/components/layout/SiteHeader';
import { HeroSection } from '@/components/layout/HeroSection';
import { ChatFocusPanel } from '@/components/chat/ChatFocusPanel';
import { FeishuBindDialog } from '@/components/chat/FeishuBindDialog';
import { useChatWindowStore } from '@/store/chat-window';

/**
 * LandingLayout —— 首页（/chat）。
 *
 *   吸顶导航（品牌行 + 黑色胶囊导航）→ 首屏 hero（左文 + 右对话示例卡 + 底部居中主按钮）
 *   → 聚焦浮窗
 *
 * 2026-11-24 调整：
 * - 去掉顶部公告条（原「数据当前为演示形态……」那一行与色块）
 * - 去掉首屏底部的常驻输入行；改为页面底部**居中放大的「开始提问」按钮**，
 *   点击后打开聚焦浮窗提问（浮窗内自带输入）
 * - 三模块为独立路由 /modules（见 ModulesPage）
 *
 * 并行线 A：飞书授权回调落地在首页 —— URL 带 `?feishu=bound` 时，把
 * `feishuPrompt` 置为 bound（复用「已连接」弹窗做成功反馈）并清理 URL，
 * 避免刷新后重复弹。
 */
export function LandingLayout() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('feishu') === 'bound') {
      useChatWindowStore.setState({ feishuPrompt: 'bound' });
      window.history.replaceState(null, '', window.location.pathname + window.location.hash);
    }
  }, []);

  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      <main className="flex flex-1 flex-col">
        <HeroSection />
      </main>
      <ChatFocusPanel />
      <FeishuBindDialog />
    </div>
  );
}
