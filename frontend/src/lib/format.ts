/** 数值展示格式化（图表轴 / 指标卡共用）。 */

/** 千分位整数。 */
export const fmtInt = (n: number): string => n.toLocaleString('zh-CN');

/** 金额（万元）→ { 数值, 单位 }：≥1 亿自动折成亿元，便于指标卡紧凑展示。 */
export function fmtAmountWan(wan: number | null): { value: string; unit: string } {
  if (wan === null) return { value: '—', unit: '' };
  if (Math.abs(wan) >= 10000) return { value: (wan / 10000).toFixed(2), unit: '亿元' };
  return { value: fmtInt(Math.round(wan)), unit: '万元' };
}

/** X 轴月份 'YYYY-MM' → 'YY/MM'（11 个刻度下更省横向空间）。 */
export const fmtMonthTick = (m: string): string => m.slice(2).replace('-', '/');
