import type { PriceRecordRow } from '@/types/chat';

/** 展示哪几列（后端 records 是通用容器，列集随子路由变化）。 */
const COLUMNS: Array<{ key: string; label: string; align?: 'right' }> = [
  { key: 'project_name', label: '项目名称' },
  { key: 'purchaser', label: '采购人' },
  { key: 'successful_bidder', label: '中标供应商' },
  { key: 'winning_amount', label: '中标金额（元）', align: 'right' },
  { key: 'winning_date', label: '中标日期', align: 'right' },
];

/**
 * PriceRecordsTable —— 对话里渲染 `price_records` 的结果表（历史中标金额参考）。
 *
 * 与图表卡里的表格不同：这是**对话结果的原始呈现**，列多且宽，
 * 容器内横向滚动（设计文档 §4.1：price_records 表格要够宽，窄屏窗内横滚）。
 */
export function PriceRecordsTable({ records }: { records: PriceRecordRow[] }) {
  if (!records.length) return null;

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-rule)]">
      <table className="w-full min-w-[34rem] border-collapse text-[12.5px]">
        <thead>
          <tr className="bg-[var(--color-paper-2)]">
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`whitespace-nowrap px-3 py-2 font-medium text-[var(--color-neutral)] ${
                  c.align === 'right' ? 'text-right' : 'text-left'
                }`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((r, i) => (
            <tr key={i} className="border-t border-[var(--color-rule)]">
              {COLUMNS.map((c) => (
                <td
                  key={c.key}
                  className={`px-3 py-2 text-[var(--color-ink-2)] ${
                    c.align === 'right' ? 'tnum whitespace-nowrap text-right' : ''
                  }`}
                >
                  {r[c.key] === null || r[c.key] === undefined || r[c.key] === ''
                    ? '—'
                    : String(r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
