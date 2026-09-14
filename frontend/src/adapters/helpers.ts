/**
 * 适配器共享工具 —— 宽容映射的原子操作。
 *
 * 后端字段类型不可信（数字可能是字符串、缺字段可能是 undefined/None），
 * 所有适配器一律经此归一，避免 `NaN`/`undefined` 漏进 Domain 层。
 */

/** 安全转数字：非有限值回落默认值。 */
export function num(v: unknown, fallback = 0): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : fallback;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v.replace(/,/g, ''));
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

/** 安全转字符串：null/undefined/空串回落默认值。 */
export function str(v: unknown, fallback = ''): string {
  if (v === null || v === undefined || v === '') return fallback;
  return String(v);
}

/** 数值钳制到 [min, max]。 */
export function clamp(v: number, min: number, max: number): number {
  return Math.min(Math.max(v, min), max);
}

/** 字符串数组归一：非数组 → []，逐项转字符串并去空。 */
export function strArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => str(x)).filter((s) => s !== '');
}

/** 首个非空值（双字段兜底：`pick(q.price, q.unitPrice)`）。 */
export function pick<T>(...values: Array<T | null | undefined>): T | undefined {
  for (const v of values) {
    if (v !== null && v !== undefined) return v;
  }
  return undefined;
}
