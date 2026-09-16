import { Table2 } from 'lucide-react';
import { fmtProjectAmount } from '@/lib/inquiry';
import type { QuoteReferenceProject } from '@/types/inquiry';

/**
 * SimilarProjectList —— 相似项目清单（右栏下半）。
 *
 * 用**领域字段**渲染而不是把原始 records 丢给 `PriceRecordsTable`：
 * 列集固定、单位统一（`fmtProjectAmount`），不会因为后端多带一列就换一张表。
 * 中标供应商作为项目名的副行——避免再加一列把表撑宽。
 */
export function SimilarProjectList({ projects }: { projects: QuoteReferenceProject[] }) {
  if (!projects.length) return null;

  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5">
      <header className="mb-3.5 flex items-center gap-1.5">
        <Table2 className="h-3.5 w-3.5 text-[var(--color-neutral)]" aria-hidden />
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
          相似项目清单
        </h2>
        <span className="text-[12px] text-[var(--color-neutral)]">（{projects.length}）</span>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full table-fixed border-collapse text-[12.5px]">
          <colgroup>
            <col className="w-[36%]" />
            <col className="w-[13%]" />
            <col className="w-[15%]" />
            <col className="w-[20%]" />
            <col className="w-[16%]" />
          </colgroup>
          <thead>
            <tr className="bg-[var(--color-paper-2)]">
              <th scope="col" className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]">
                项目名称
              </th>
              <th scope="col" className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]">
                地区
              </th>
              <th scope="col" className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]">
                类别
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium text-[var(--color-neutral)]">
                中标金额
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium text-[var(--color-neutral)]">
                中标日期
              </th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.projectId} className="border-t border-[var(--color-rule)]">
                <td className="px-3 py-2">
                  <p className="truncate text-[var(--color-ink-2)]" title={p.projectName}>
                    {p.projectName}
                  </p>
                  {p.winningBidder && (
                    <p className="truncate text-[11.5px] text-[var(--color-neutral)]" title={p.winningBidder}>
                      {p.winningBidder}
                    </p>
                  )}
                </td>
                <td className="truncate px-3 py-2 text-[var(--color-ink-2)]">{p.region}</td>
                <td className="truncate px-3 py-2 text-[var(--color-muted)]">{p.category ?? '—'}</td>
                <td className="tnum truncate px-3 py-2 text-right text-[var(--color-ink-2)]">
                  {fmtProjectAmount(p.winningAmount)}
                </td>
                <td className="tnum truncate px-3 py-2 text-right text-[var(--color-muted)]">
                  {p.winningDate}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
