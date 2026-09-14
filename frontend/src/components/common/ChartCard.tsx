import { useState, type ReactNode } from 'react';
import { BarChart3, Table2 } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * ChartCard —— 图表卡片外壳：标题 + 副标题 + **图表/表格 双视图**。
 *
 * dataviz 无障碍要求：每张图都要有「表格孪生」（值可读、不依赖颜色）。
 * 表格视图由调用方通过 `table` 传入（同一份数据，无障碍可读）。
 */
export function ChartCard({
  title,
  subtitle,
  table,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  /** 表格孪生视图（无障碍）；不传则不显示切换按钮 */
  table?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [showTable, setShowTable] = useState(false);

  return (
    <section
      className={cn(
        'flex flex-col rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5',
        className
      )}
    >
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            {title}
          </h2>
          {subtitle && (
            <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--color-neutral)]">
              {subtitle}
            </p>
          )}
        </div>
        {table && (
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            aria-pressed={showTable}
            className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
          >
            {showTable ? (
              <>
                <BarChart3 className="h-3.5 w-3.5" aria-hidden />
                图表
              </>
            ) : (
              <>
                <Table2 className="h-3.5 w-3.5" aria-hidden />
                表格
              </>
            )}
          </button>
        )}
      </header>
      <div className="min-w-0 flex-1">{showTable && table ? table : children}</div>
    </section>
  );
}

/** 表格孪生视图：素表，等宽数字列对齐。 */
export function DataTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Array<Array<string | number>>;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-[var(--color-rule)]">
            {columns.map((c) => (
              <th
                key={c}
                scope="col"
                className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-[var(--color-rule)] last:border-0">
              {row.map((cell, j) => (
                <td
                  key={j}
                  className={cn(
                    'px-3 py-1.5 text-[var(--color-ink-2)]',
                    j > 0 && 'tnum text-right'
                  )}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
