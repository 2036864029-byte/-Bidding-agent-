import { BookOpenText } from 'lucide-react';
import { PlaceholderPage } from '@/components/common/PlaceholderPage';

/** 功能介绍（占位页，内容后续填充） */
export function IntroPage() {
  return (
    <PlaceholderPage
      icon={BookOpenText}
      title="功能介绍"
      description="四大核心能力将在此展开说明：招标法规知识问答、价格与标讯查询、通用对话、文档问答。"
    />
  );
}
