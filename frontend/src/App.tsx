import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import { MotionConfig } from 'framer-motion';
import { ChatPage } from '@/pages/chat/ChatPage';
import { InquiryPage } from '@/pages/inquiry/InquiryPage';
import { KanbanPage } from '@/pages/kanban/KanbanPage';
import { SupplierPage } from '@/pages/supplier/SupplierPage';
import { IntroPage } from '@/pages/intro/IntroPage';
import { FaqPage } from '@/pages/faq/FaqPage';
import { MobilePage } from '@/pages/mobile/MobilePage';
import { LinksPage } from '@/pages/links/LinksPage';

/**
 * 路由骨架（Phase 1 + 主页面优化）：
 *   /chat      对话控制台（首页，四层落地页）
 *   /inquiry   智能询价（Phase 3 落地）
 *   /kanban    商机看板（Phase 3 落地）
 *   /supplier  供应商全景（Phase 3 落地）
 *   /intro /faq /mobile /links   导航占位页（内容后续填充）
 * MotionConfig reducedMotion="user"：Framer Motion 尊重系统减动效偏好
 */
export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <BrowserRouter>
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
      </BrowserRouter>
    </MotionConfig>
  );
}
