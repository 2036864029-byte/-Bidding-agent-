/** 数值展示格式化（图表轴 / 指标卡共用）。 */

/** 千分位整数。 */
export const fmtInt = (n: number): string => n.toLocaleString('zh-CN');

/** 金额（万元）→ { 数值, 单位 }：≥1 亿自动折成亿元，便于指标卡紧凑展示。 */
export function fmtAmountWan(wan: number | null): { value: string; unit: string } {
  if (wan === null) return { value: '—', unit: '' };
  if (Math.abs(wan) >= 10000) return { value: (wan / 10000).toFixed(2), unit: '亿元' };
  return { value: fmtInt(Math.round(wan)), unit: '万元' };
}

/**
 * 金额（**元**）→ { 数值, 单位 }：≥1 亿折亿元、≥1 万折万元。
 *
 * ⚠️ 与 `fmtAmountWan` **不是**同一个东西，别混用：招投标系统里汇总金额用万元、
 * 但询价报价与 `price_records` 用元（见 types/inquiry.ts 文件头）。传错单位会差 10000 倍。
 */
export function fmtAmountYuan(yuan: number | null): { value: string; unit: string } {
  if (yuan === null) return { value: '—', unit: '' };
  if (Math.abs(yuan) >= 100_000_000) return { value: (yuan / 100_000_000).toFixed(2), unit: '亿元' };
  if (Math.abs(yuan) >= 10_000) return { value: (yuan / 10_000).toFixed(2), unit: '万元' };
  return { value: fmtInt(Math.round(yuan)), unit: '元' };
}

/** 万元金额（固定 2 位小数，报价参考用）→ 文案；≥1 亿自动折亿元。
 *
 * 与 `fmtAmountWan` 的区别：后者用于指标卡紧凑展示（取整），这里保留小数——
 * 报价参考区间必须精确到分，round 到整万会丢掉 42.50 万这种有效信息。
 */
export function fmtWanFixed(wan: number): string {
  const big = Math.abs(wan) >= 10000;
  const value = big ? wan / 10000 : wan;
  const unit = big ? '亿元' : '万元';
  return `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
}

/** X 轴月份 'YYYY-MM' → 'YY/MM'（11 个刻度下更省横向空间）。 */
export const fmtMonthTick = (m: string): string => m.slice(2).replace('-', '/');
