import { lazy, Suspense, type ComponentType } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import { MotionConfig } from 'framer-motion';
import { RouteFallback } from '@/components/layout/RouteFallback';
import { ChatPage } from '@/pages/chat/ChatPage';
import { IntroPage } from '@/pages/intro/IntroPage';
import { FaqPage } from '@/pages/faq/FaqPage';
import { MobilePage } from '@/pages/mobile/MobilePage';
import { LinksPage } from '@/pages/links/LinksPage';

/**
 * 路由骨架。
 *   /chat      对话控制台（首页，四层落地页）
 *   /inquiry   智能询价    /kanban 商机看板    /supplier 供应商全景
 *   /intro /faq /mobile /links   导航占位页（内容后续填充）
 * MotionConfig reducedMotion="user"：Framer Motion 尊重系统减动效偏好
 *
 * **路由级懒加载**：三个业务模块按需拉取 —— 它们依赖 Recharts（charts chunk ~438KB），
 * 而落地页/对话页根本用不到图表；懒加载后首屏不再背这个包袱。
 * 页面组件是具名导出，故此处做一次 default 映射。
 */
const lazyNamed = <T extends Record<string, unknown>>(
  loader: () => Promise<T>,
  name: keyof T
) =>
  lazy(async () => {
    const mod = await loader();
    return { default: mod[name] as ComponentType };
  });

const KanbanPage = lazyNamed(() => import('@/pages/kanban/KanbanPage'), 'KanbanPage');
const InquiryPage = lazyNamed(() => import('@/pages/inquiry/InquiryPage'), 'InquiryPage');
const SupplierPage = lazyNamed(() => import('@/pages/supplier/SupplierPage'), 'SupplierPage');

export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/inquiry" element={<InquiryPage />} />
            <Route path="/kanban" element={<KanbanPage />} />
            <Route path="/supplier" element={<SupplierPage />} />
            <Route path="/intro" element={<IntroPage />} />
            <Route path="/faq" element={<FaqPage />} />
            <Route path="/mobile" element={<MobilePage />} />
            <Route path="/links" element={<LinksPage />} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </MotionConfig>
  );
}
