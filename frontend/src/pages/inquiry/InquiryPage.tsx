import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { Calculator, MessageSquareQuote, X } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { Badge } from '@/components/ui/badge';
import { InquiryChat } from '@/components/inquiry/InquiryChat';
import { ProjectReferencePanel } from '@/components/inquiry/ProjectReferencePanel';
import { ReferenceSummary } from '@/components/inquiry/ReferenceSummary';
import { SimilarProjectList } from '@/components/inquiry/SimilarProjectList';
import { buildReference } from '@/lib/inquiry';
import type { ChatResponse } from '@/types/chat';
import type { QuoteReference } from '@/types/inquiry';

/**
 * 智能询价 —— 「项目概况 → 报价参考」。
 *
 * 与旧版（询价单 + 多方报价对比 + 偏离高亮）的区别：右栏**不再自成一套假数据**，
 * 而是由左栏那一次查询的结果实时派生 —— 用户问什么，右栏就显示什么。
 *
 *   左栏 InquiryChat ──onResult──▶ buildReference(records) ──▶ 右栏区间卡 + 相似项目清单
 *
 * 数据源是 `/chat` 的 `price_records.records`（真实来源 `ztb_clean.bid_project`），
 * 所以**首屏是空态**：没有查询就没有参考，不预填编造的项目。
 */

/** 参考卡标题用提问本身，过长截断。 */
function titleOf(question: string): string {
  const q = question.trim();
  return q.length > 30 ? `${q.slice(0, 30)}…` : q;
}

export function InquiryPage() {
  const [reference, setReference] = useState<QuoteReference | null>(null);

  // 对话模块联动入口：/inquiry?from=chat&q=<原始提问>
  const [searchParams, setSearchParams] = useSearchParams();
  const [bannerOpen, setBannerOpen] = useState(true);
  const fromChat = searchParams.get('from') === 'chat';
  const carriedQuestion = searchParams.get('q') ?? '';
  const dismissBanner = () => {
    setBannerOpen(false);
    setSearchParams({}, { replace: true }); // 清掉参数，刷新不再弹
  };

  /**
   * 只认价格类结果：用户中途问了个知识问题（kb_answer）时**保留上一次参考**，
   * 而不是把右栏清空——那会让人以为数据丢了。
   */
  const handleResult = (res: ChatResponse, question: string) => {
    if (res.data?.kind !== 'price_records') return;
    const next = buildReference(res.data.records, titleOf(question));
    if (next.projects.length > 0) setReference(next);
  };

  const badge = (
    <Badge variant="accent">
      <Calculator className="h-3 w-3" aria-hidden />
      对话区接真实 /chat · 报价参考由查询结果实时派生
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="智能询价"
      eyebrow="INTELLIGENT INQUIRY"
      description="描述项目概况或上传招标文件，系统检索相似历史项目，给出中标价参考区间。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  return shell(
    <>
      {fromChat && bannerOpen && (
        <div className="mb-4 flex items-start gap-3 rounded-2xl border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-4 py-3">
          <MessageSquareQuote className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent)]" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--color-ink)]">来自对话</p>
            {carriedQuestion && (
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
                「{carriedQuestion}」
              </p>
            )}
            <p className="mt-1 text-[12px] text-[var(--color-neutral)]">
              已把这个问题带到左侧项目概况区，可直接发送继续查询。
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

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        <InquiryChat initialQuestion={carriedQuestion} onResult={handleResult} />

        <div className="space-y-4">
          {/* 独立端点链路：粘贴项目概况 → 相似中标 + 金额参考（万元） */}
          <ProjectReferencePanel />

          {/* 聊天派生链路（保留）：左栏 /chat 结果 → 报价参考（元） */}
          {reference && (
            <>
              <ReferenceSummary reference={reference} />
              <SimilarProjectList projects={reference.projects} />
            </>
          )}
        </div>
      </div>
    </>
  );
}
