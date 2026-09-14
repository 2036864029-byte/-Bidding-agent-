import { HelpCircle } from 'lucide-react';
import { PlaceholderPage } from '@/components/common/PlaceholderPage';

/** 常见问题（占位页，内容后续填充） */
export function FaqPage() {
  return (
    <PlaceholderPage
      icon={HelpCircle}
      title="常见问题"
      description="高频问题将在此汇总，如：招标方式有哪些？如何查询历史中标价格？"
    />
  );
}
