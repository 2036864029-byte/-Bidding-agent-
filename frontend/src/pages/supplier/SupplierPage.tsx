import { useEffect, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { Building2, MessageSquareQuote, SearchX, WifiOff, X } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { EmptyState } from '@/components/common/EmptyState';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SupplierCard } from '@/components/supplier/SupplierCard';
import { SupplierDetailModal } from '@/components/supplier/SupplierDetailModal';
import { FILTER_ALL, SupplierFilterRow } from '@/components/supplier/SupplierFilterRow';
import { useAsyncData } from '@/hooks/use-async-data';
import { fmtInt } from '@/lib/format';
import { api, dataConfig } from '@/services/api';
import type { Supplier } from '@/types/supplier';

/**
 * 供应商全景 —— **按地区 / 行业筛选，点开看工商信息与中标情况**。
 *
 * 2026-09-15 重塑。原页面是「能力评分卡」（评分 + 五维雷达 + 资质/风险标签），
 * 那四项**没有任何数据源**，已整体去掉；改为可核验的**工商档案 + 中标业绩**，
 * 数据来自 `company_unified`（38,911 家工商库 ∪ 1,058 家新数据）。
 *
 * ⚠️ 两个必须让用户看见的口径：
 *   1. 全量 39,969 家，一页只返回 60 家 —— 页头如实显示「共 N 家，显示前 M 家」。
 *   2. 工商字段只有 LEGACY 侧有，CCGP 侧那 1,058 家没有省份/行业，
 *      **按地区/行业筛选时不会出现**（卡片上会明说"暂无工商信息"）。
 */

const PAGE_SIZE = 60;
const KEYWORD_DEBOUNCE_MS = 350;

export function SupplierPage() {
  const [province, setProvince] = useState(FILTER_ALL);
  const [industry, setIndustry] = useState(FILTER_ALL);
  // 输入框即时响应，真正发请求的条件延后（否则每敲一个字打一次后端）
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');

  // 对话模块联动入口：/supplier?from=chat&q=<公司名> → 预填关键词筛选 + 来自对话横幅
  const [searchParams, setSearchParams] = useSearchParams();
  const [bannerOpen, setBannerOpen] = useState(true);
  const fromChat = searchParams.get('from') === 'chat';
  const carriedKeyword = searchParams.get('q') ?? '';
  useEffect(() => {
    if (carriedKeyword) {
      setKeywordInput(carriedKeyword);
      setKeyword(carriedKeyword);
    }
  }, [carriedKeyword]);
  const dismissBanner = () => {
    setBannerOpen(false);
    setSearchParams({}, { replace: true }); // 清掉参数，刷新不再弹
  };

  useEffect(() => {
    const t = setTimeout(() => setKeyword(keywordInput.trim()), KEYWORD_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [keywordInput]);

  const query = {
    province: province === FILTER_ALL ? undefined : province,
    industry: industry === FILTER_ALL ? undefined : industry,
    keyword: keyword || undefined,
    limit: PAGE_SIZE,
  };

  const { data, error, loading } = useAsyncData(
    () => api.supplier.getOverview(query),
    JSON.stringify(query)
  );

  const [detail, setDetail] = useState<Supplier | null>(null);

  const reset = () => {
    setProvince(FILTER_ALL);
    setIndustry(FILTER_ALL);
    setKeywordInput('');
    setKeyword('');
  };

  const live = dataConfig.supplier === 'api';

  const badge = (
    <Badge variant="accent">
      <Building2 className="h-3 w-3" aria-hidden />
      {live ? '真实数据 · 工商档案 + 中标业绩' : 'Mock 数据'}
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="供应商全景"
      eyebrow="SUPPLIER INTELLIGENCE"
      description="按地区、行业筛选企业，点开查看工商信息与中标情况。数据来自工商档案库与新采集公告的合并。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  if (loading) {
    return shell(
      <div className="space-y-4" aria-busy="true" aria-live="polite">
        <div className="h-16 animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-[15rem] animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
            />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return shell(
      <EmptyState
        icon={WifiOff}
        title="暂时拿不到供应商数据"
        description={error?.message ?? '数据源返回为空，请稍后重试。'}
      />
    );
  }

  return shell(
    <div className="space-y-4">
      {fromChat && bannerOpen && (
        <div className="flex items-start gap-3 rounded-2xl border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-4 py-3">
          <MessageSquareQuote className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent)]" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-medium text-[var(--color-ink)]">来自对话</p>
            {carriedKeyword && (
              <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
                「{carriedKeyword}」
              </p>
            )}
            <p className="mt-1 text-[12px] text-[var(--color-neutral)]">
              已按这家企业筛选，可查看工商信息与中标情况；也可在上方调整地区、行业或关键词。
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

      <SupplierFilterRow
        provinces={data.filters.provinces}
        industries={data.filters.industries}
        province={province}
        industry={industry}
        keyword={keywordInput}
        onChange={(next) => {
          if (next.province !== undefined) setProvince(next.province);
          if (next.industry !== undefined) setIndustry(next.industry);
          if (next.keyword !== undefined) setKeywordInput(next.keyword);
        }}
        onReset={reset}
      />

      <p className="px-1 text-[12.5px] text-[var(--color-neutral)]">
        共 <span className="tnum font-medium text-[var(--color-ink-2)]">{fmtInt(data.total)}</span> 家
        {data.returned < data.total && (
          <>
            ，按中标次数倒序显示前{' '}
            <span className="tnum font-medium text-[var(--color-ink-2)]">{data.returned}</span> 家
          </>
        )}
      </p>

      {data.suppliers.length === 0 ? (
        <EmptyState
          icon={SearchX}
          title="没有符合条件的企业"
          description={
            province !== FILTER_ALL || industry !== FILTER_ALL
              ? '当前地区/行业组合没有结果。注意：新采集的企业尚未并入工商档案，没有地区与行业信息，按这两项筛选时不会出现。'
              : '换个关键词试试，或重置筛选。'
          }
          action={
            <Button variant="ghost" size="sm" onClick={reset}>
              重置筛选
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data.suppliers.map((s) => (
            <SupplierCard key={s.id} supplier={s} onOpenDetail={setDetail} />
          ))}
        </div>
      )}

      <SupplierDetailModal supplier={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
