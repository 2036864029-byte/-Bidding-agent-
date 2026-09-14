import { Smartphone } from 'lucide-react';
import { PlaceholderPage } from '@/components/common/PlaceholderPage';

/** 移动端连接（占位页，内容后续填充） */
export function MobilePage() {
  return (
    <PlaceholderPage
      icon={Smartphone}
      title="移动端连接"
      description="移动端访问入口将在此提供（扫码或下载），后续填充。"
    />
  );
}
