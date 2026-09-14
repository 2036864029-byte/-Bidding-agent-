import type { InquiryItem, InquiryQuote } from '@/types/inquiry';

/**
 * 询价对比的纯逻辑（无副作用，可单测）。
 *
 * 偏离度一律**由展示用的单价与基准现算**（而不是取后端给的 deviationRate），
 * 这样表格里显示的百分比永远和旁边的两个价格自洽——否则后端字段一漂，
 * 用户就会看到「价格超阈值、偏离度却显示 0.0%」这种自相矛盾的行。
 */

export type DeviationLevel = 'over' | 'above' | 'below';

/** 对基准单价的偏离度 %（正 = 高于基准）。 */
export function computeDeviation(unitPrice: number, benchmarkUnitPrice: number): number {
  if (benchmarkUnitPrice <= 0) return 0;
  return ((unitPrice - benchmarkUnitPrice) / benchmarkUnitPrice) * 100;
}

/**
 * 偏离等级：**超阈值** > 高于基准 > 低于基准。
 * 超阈值即触发高亮（设计文档 §4.4 的「偏离度高亮」）。
 */
export function deviationLevel(
  unitPrice: number,
  item: Pick<InquiryItem, 'benchmarkUnitPrice' | 'budgetThreshold'>
): DeviationLevel {
  if (item.budgetThreshold > 0 && unitPrice > item.budgetThreshold) return 'over';
  if (unitPrice > item.benchmarkUnitPrice) return 'above';
  return 'below';
}

/** 带符号的百分比文本（+22.4% / -3.5% / 0.0%）。 */
export function fmtDeviation(rate: number): string {
  const sign = rate > 0 ? '+' : '';
  return `${sign}${rate.toFixed(1)}%`;
}

/** 报价按单价升序（对比表默认序）。 */
export function sortQuotes(quotes: InquiryQuote[]): InquiryQuote[] {
  return [...quotes].sort((a, b) => a.unitPrice - b.unitPrice);
}

/** 要把单价降到阈值以内，还需下调多少（元/单位）；已在阈值内则返回 0。 */
export function reductionToThreshold(
  unitPrice: number,
  item: Pick<InquiryItem, 'budgetThreshold'>
): number {
  if (item.budgetThreshold <= 0 || unitPrice <= item.budgetThreshold) return 0;
  return unitPrice - item.budgetThreshold;
}

/** 单项的横向对比小结（表头用）。 */
export function itemStats(item: InquiryItem): {
  quoteCount: number;
  lowest: number;
  highest: number;
  overThreshold: number;
} {
  const prices = item.quotes.map((q) => q.unitPrice);
  const lowest = prices.length ? Math.min(...prices) : 0;
  const highest = prices.length ? Math.max(...prices) : 0;
  const overThreshold = item.quotes.filter(
    (q) => deviationLevel(q.unitPrice, item) === 'over'
  ).length;
  return { quoteCount: item.quotes.length, lowest, highest, overThreshold };
}
