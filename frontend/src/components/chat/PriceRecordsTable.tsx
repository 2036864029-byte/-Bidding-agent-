import type { PriceColumn, PriceRecordRow } from '@/types/chat';

/**
 * PriceRecordsTable —— 对话里渲染 `price_records` 的结果表。
 *
 * **列优先用服务端下发的 `columns`**（`{key, label}`，顺序即 display_order）——
 * 那是权威映射，后端增删字段/改表头前端自动跟随，不需要改这里的代码。
 * 服务端没给（旧后端 / 老 mock）时才回落到下面的启发式。
 *
 * ⚠️ 回落启发式：`records` 是**通用容器**，列集随子路由变化：
 *   bidding_query → 项目名称/采购人/中标供应商/中标金额/中标日期
 *   company_query → 企业名称/信用代码/法定代表人/注册资本/经营状态…
 *   penalty 联查   → 处罚日期/违法行为/处罚结果
 * 所以**不能写死列**（写死会导致企业查询整表都是「—」——实测踩过），
 * 而要按实际字段自适应：优先列在前，其余按出现顺序补位，整列为空的丢掉。
 */

/** 优先展示的字段（按业务可读性排序）；未列出的字段按出现顺序补在后面。 */
const PREFERRED = [
  'project_name',
  'project_number',
  'purchaser',
  'successful_bidder',
  'winning_amount',
  'winning_date',
  'company_name',
  'credit_code',
  'legal_person',
  'registered_capital',
  'business_status',
  'industry',
  'penalty_date',
  'illegal_behavior',
  'penalty_result',
];

const LABELS: Record<string, string> = {
  project_name: '项目名称',
  project_number: '项目编号',
  purchaser: '采购人',
  successful_bidder: '中标供应商',
  winning_amount: '中标金额（元）',
  winning_date: '中标日期',
  company_name: '企业名称',
  credit_code: '统一社会信用代码',
  legal_person: '法定代表人',
  registered_capital: '注册资本',
  business_status: '经营状态',
  industry: '所属行业',
  company_type: '企业类型',
  company_level: '企业等级',
  province: '省份',
  city: '城市',
  address: '企业地址',
  business_scope: '经营范围',
  penalty_date: '处罚日期',
  illegal_behavior: '违法行为',
  penalty_result: '处罚结果',
  law_enforcement_unit: '执法单位',
  agent: '代理机构',
  project_stage: '项目阶段',
  project_category: '项目类别',
  budget_amount: '预算金额（元）',
  subject_matter: '标的物',
};

/** 金额类字段右对齐（数字列） */
const NUMERIC = new Set(['winning_amount', 'budget_amount', 'registered_capital', 'project_number', 'credit_code']);

/** 最多展示几列 —— 再多就不是"速览"而是"倾倒"，长内容交给正文。 */
const MAX_COLUMNS = 6;

const hasValue = (v: unknown): boolean => v !== null && v !== undefined && v !== '';

/** 按实际数据推导列：优先列 → 其余字段；丢掉整列为空的列。 */
export function deriveColumns(records: PriceRecordRow[]): string[] {
  if (!records.length) return [];
  const present: string[] = [];
  for (const r of records) {
    for (const k of Object.keys(r)) {
      if (!present.includes(k)) present.push(k);
    }
  }
  const ordered = [
    ...PREFERRED.filter((k) => present.includes(k)),
    ...present.filter((k) => !PREFERRED.includes(k)),
  ];
  return ordered.filter((k) => records.some((r) => hasValue(r[k]))).slice(0, MAX_COLUMNS);
}

/**
 * 定列：**服务端 `columns` 优先**（权威，且已按 display_order 排好），
 * 缺失或与实际数据完全对不上时回落 `deriveColumns` + 本地 LABELS。
 */
export function resolveColumns(
  records: PriceRecordRow[],
  columns?: PriceColumn[]
): PriceColumn[] {
  if (columns?.length) {
    const used = columns.filter((c) => records.some((r) => hasValue(r[c.key])));
    if (used.length) {
      return used
        .map((c) => ({ key: c.key, label: c.label || LABELS[c.key] || c.key }))
        .slice(0, MAX_COLUMNS);
    }
  }
  return deriveColumns(records).map((k) => ({ key: k, label: LABELS[k] ?? k }));
}

export function PriceRecordsTable({
  records,
  columns,
}: {
  records: PriceRecordRow[];
  columns?: PriceColumn[];
}) {
  if (!records.length) return null;
  const cols = resolveColumns(records, columns);
  if (!cols.length) return null;

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-rule)]">
      <table className="w-full min-w-[28rem] border-collapse text-[12.5px]">
        <thead>
          <tr className="bg-[var(--color-paper-2)]">
            {cols.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`whitespace-nowrap px-3 py-2 font-medium text-[var(--color-neutral)] ${
                  NUMERIC.has(c.key) ? 'text-right' : 'text-left'
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
              {cols.map((c) => (
                <td
                  key={c.key}
                  className={`px-3 py-2 text-[var(--color-ink-2)] ${
                    NUMERIC.has(c.key) ? 'tnum whitespace-nowrap text-right' : ''
                  }`}
                >
                  {hasValue(r[c.key]) ? String(r[c.key]) : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
