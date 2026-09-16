import { Search } from 'lucide-react';
import { Button } from '@/components/ui/button';

export const FILTER_ALL = '__all__';

/**
 * SupplierFilterRow —— 供应商筛选：**地区 / 行业 / 关键词**。
 *
 * 2026-09-15 重塑：原筛选器按「资质 / 类别 / 评分区间」——这三样都没有数据源，
 * 已换成真实存在的维度。筛选走**服务端**（39,969 家不可能全下到前端再筛）。
 */
export function SupplierFilterRow({
  provinces,
  industries,
  province,
  industry,
  keyword,
  onChange,
  onReset,
}: {
  provinces: string[];
  industries: string[];
  province: string;
  industry: string;
  keyword: string;
  onChange: (next: { province?: string; industry?: string; keyword?: string }) => void;
  onReset: () => void;
}) {
  const dirty = province !== FILTER_ALL || industry !== FILTER_ALL || keyword !== '';

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-4 py-3">
      <span className="mono-label">筛选</span>

      <label className="flex items-center gap-1.5 text-[12.5px] text-[var(--color-muted)]">
        地区
        <select
          value={province}
          onChange={(e) => onChange({ province: e.target.value })}
          aria-label="按地区筛选"
          className="rounded-lg border border-[var(--color-rule-2)] bg-[var(--color-paper)] px-2 py-1.5 text-[12.5px] text-[var(--color-ink-2)]"
        >
          <option value={FILTER_ALL}>全部</option>
          {provinces.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-1.5 text-[12.5px] text-[var(--color-muted)]">
        行业
        <select
          value={industry}
          onChange={(e) => onChange({ industry: e.target.value })}
          aria-label="按行业筛选"
          className="max-w-[14rem] rounded-lg border border-[var(--color-rule-2)] bg-[var(--color-paper)] px-2 py-1.5 text-[12.5px] text-[var(--color-ink-2)]"
        >
          <option value={FILTER_ALL}>全部</option>
          {industries.map((i) => (
            <option key={i} value={i}>
              {i}
            </option>
          ))}
        </select>
      </label>

      <label className="flex min-w-[12rem] flex-1 items-center gap-1.5 rounded-lg border border-[var(--color-rule-2)] px-2.5 py-1">
        <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-neutral)]" aria-hidden />
        <input
          value={keyword}
          onChange={(e) => onChange({ keyword: e.target.value })}
          placeholder="按企业名称搜索"
          aria-label="按企业名称搜索"
          className="min-w-0 flex-1 bg-transparent text-[12.5px] text-[var(--color-ink)] outline-none placeholder:text-[var(--color-neutral)]"
        />
      </label>

      {dirty && (
        <Button variant="ghost" size="sm" onClick={onReset}>
          重置
        </Button>
      )}
    </div>
  );
}
