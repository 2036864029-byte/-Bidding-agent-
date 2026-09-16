import { fmtInt } from '@/lib/format';
import type { PriceRecordRow } from '@/types/chat';
import type { QuoteReference, QuoteReferenceProject } from '@/types/inquiry';

/**
 * 报价参考的纯逻辑（无副作用，可单测）。
 *
 * 输入是左侧 `/chat` 返回的 `price_records.records`（真实来源 `bid_project`），
 * 输出是右栏直接渲染的 `QuoteReference`。整条链没有后端端点、没有 mock 分支
 * —— 问答返回什么就算什么，所以**问一句变一次**，与 Context 的闭环一致。
 */

/** 金额列候选（后端列集随子路由变化，按序取第一个命中）。 */
const AMOUNT_KEYS = ['winning_amount', 'winningAmount', 'award_amount', 'awardAmount'];

/**
 * 抽单行金额（元）；抽不到返回 null。
 *
 * ⚠️ 必须容忍千分位串：后端 `winning_amount` 实测形如 `'4,860,000.00'`。
 */
function toAmount(row: PriceRecordRow): number | null {
  for (const k of AMOUNT_KEYS) {
    const v = row[k];
    if (v === null || v === undefined || v === '') continue;
    const n =
      typeof v === 'number' ? v : Number(String(v).replace(/[,\s¥￥]/g, ''));
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/**
 * 抽出所有**有效**中标金额（元）。
 *
 * ⚠️ 跳过 0：`price_engine/rendering/answer_templates.py` 明确 0 值的语义是
 * 「金额未公开」。把 0 计入会让区间下限变成 0，整张卡失真。
 */
export function pickAmounts(records: PriceRecordRow[]): number[] {
  const out: number[] = [];
  for (const r of records) {
    const n = toAmount(r);
    if (n !== null && n > 0) out.push(n);
  }
  return out;
}

/** 区间统计（元）。空数组 → 全 0（不产生 NaN/Infinity）。 */
export function referenceStats(amounts: number[]): {
  min: number;
  median: number;
  max: number;
  sampleCount: number;
} {
  if (!amounts.length) return { min: 0, median: 0, max: 0, sampleCount: 0 };

  const sorted = [...amounts].sort((a, b) => a - b);
  const n = sorted.length;
  const mid = n >> 1;
  // 奇数取中间，偶数取中间两个的均值
  const median = n % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;

  return { min: sorted[0], median, max: sorted[n - 1], sampleCount: n };
}

const str = (v: unknown): string | undefined => {
  if (v === null || v === undefined) return undefined;
  const s = String(v).trim();
  return s && s !== '—' ? s : undefined;
};

/** 单行 → 相似项目（缺列一律兜底，不让 UI 出现 undefined）。 */
function toProject(row: PriceRecordRow, index: number): QuoteReferenceProject {
  return {
    projectId: str(row.project_number) ?? str(row.projectId) ?? `#${index + 1}`,
    projectName: str(row.project_name) ?? str(row.projectName) ?? '（未命名项目）',
    region: str(row.province) ?? str(row.region) ?? '—',
    category: str(row.project_category) ?? str(row.category),
    purchaser: str(row.purchaser),
    winningBidder: str(row.successful_bidder) ?? str(row.winningBidder),
    winningAmount: toAmount(row) ?? 0,
    winningDate: str(row.winning_date) ?? str(row.winningDate) ?? '—',
  };
}

/** records → 报价参考。 */
export function buildReference(records: PriceRecordRow[], title = '本次查询'): QuoteReference {
  const amounts = pickAmounts(records);
  const { min, median, max, sampleCount } = referenceStats(amounts);
  return {
    title,
    minAmount: min,
    medianAmount: median,
    maxAmount: max,
    sampleCount,
    missingAmountCount: records.length - amounts.length,
    projects: records.map(toProject),
  };
}

/** 区间展示的单位：**按最大值定一次**，两端同单位（否则会出现「8,500 元 ~ 512.00 万元」）。 */
function commonUnit(max: number): { div: number; unit: string; digits: number } {
  if (Math.abs(max) >= 100_000_000) return { div: 100_000_000, unit: '亿元', digits: 2 };
  if (Math.abs(max) >= 10_000) return { div: 10_000, unit: '万元', digits: 2 };
  return { div: 1, unit: '元', digits: 0 };
}

/** 定长小数 + 千分位整数部分（`fmtInt` 会吃掉末尾 0，这里要保留）。 */
function fmtFixed(n: number, div: number, digits: number): string {
  const [int, frac] = (n / div).toFixed(digits).split('.');
  const grouped = fmtInt(Number(int));
  return frac ? `${grouped}.${frac}` : grouped;
}

/** 区间文案，如「288.00 ~ 512.00 万元」；无有效金额时给明确提示。 */
export function fmtReferenceRange(ref: Pick<QuoteReference, 'minAmount' | 'maxAmount' | 'sampleCount'>): string {
  if (!ref.sampleCount) return '暂无可用的中标金额';
  const { div, unit, digits } = commonUnit(ref.maxAmount);
  return `${fmtFixed(ref.minAmount, div, digits)} ~ ${fmtFixed(ref.maxAmount, div, digits)} ${unit}`;
}

/** 单笔金额文案（相似项目清单的行内展示），复用同一套单位判定。 */
export function fmtProjectAmount(amount: number): string {
  if (amount <= 0) return '金额未公开';
  const { div, unit, digits } = commonUnit(amount);
  return `${fmtFixed(amount, div, digits)} ${unit}`;
}
