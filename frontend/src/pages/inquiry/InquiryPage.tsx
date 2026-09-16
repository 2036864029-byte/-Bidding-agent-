import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { Calculator, MessageSquareQuote, X } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { Badge } from '@/components/ui/badge';
import { ProjectReferencePanel } from '@/components/inquiry/ProjectReferencePanel';

/**
 * 智能询价 —— 「项目概况 → 报价参考」。
 *
 * 唯一链路：粘贴/输入一段招标项目概况 → `POST /inquiry/reference` 语义检索历史
 * 中标公告 → 返回相似项目 + 金额区间（万元）+ 原文链接 + 产品明细。
 *
 * 2026-09-16 改版：移除左侧对话控制台。原「左栏 /chat 提问 → 右栏派生报价参考」
 * 的旧链路随之下线（报价参考已由独立端点直接提供，金额统一万元、带原文链接，
 * 比聊天派生的元口径视图更完整）；页面收敛为单列主区 —— 上方输入概况，下方出参考。
 */

export function InquiryPage() {
  // 对话模块联动入口：/inquiry?from=chat&q=<原始提问> → 预填下方输入框
  const [searchParams, setSearchParams] = useSearchParams();
  const [bannerOpen, setBannerOpen] = useState(true);
  const fromChat = searchParams.get('from') === 'chat';
  const carriedQuestion = searchParams.get('q') ?? '';
  const dismissBanner = () => {
    setBannerOpen(false);
    setSearchParams({}, { replace: true }); // 清掉参数，刷新不再弹
  };

  const badge = (
    <Badge variant="accent">
      <Calculator className="h-3 w-3" aria-hidden />
      语义检索历史中标 · 金额统一万元
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="智能询价"
      eyebrow="INTELLIGENT INQUIRY"
      description="描述项目概况，系统检索相似历史项目，给出中标价参考区间。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  return shell(
    <div className="mx-auto w-full max-w-3xl space-y-4">
      {fromChat && bannerOpen && (
        <div className="flex items-start gap-3 rounded-2xl border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-4 py-3">
          <MessageSquareQuote className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent)]" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--color-ink)]">来自对话</p>
            {carriedQuestion && (
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
                「{carriedQuestion}」
              </p>
            )}
            <p className="mt-1 text-[12px] text-[var(--color-neutral)]">
              已把这个问题带到下方的项目概况输入框，可直接点击「查金额参考」。
            </p>
          </div>
          <button
            type="button"
            onClick={dismissBanner}
            aria-label="关闭提示"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper)] hover:text-[var(--color-ink)]"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      )}

      <ProjectReferencePanel initialText={carriedQuestion} />
    </div>
  );
}
