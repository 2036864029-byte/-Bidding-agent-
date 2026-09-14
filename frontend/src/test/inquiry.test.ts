import { describe, expect, it } from 'vitest';
import { inquiryAdapter } from '@/adapters/inquiryAdapter';
import {
  computeDeviation,
  deviationLevel,
  fmtDeviation,
  itemStats,
  reductionToThreshold,
  sortQuotes,
} from '@/lib/inquiry';
import { mockInquiryRaw } from '@/mock/mockInquiry';

/* 询价对比的纯逻辑：偏离度、等级判定（超阈值高亮的依据）、降幅测算。 */

const summary = inquiryAdapter(mockInquiryRaw);
// 摄像机：基准 850 / 阈值 1000 / 报价 820、875、1040
const camera = summary.items[0];

describe('computeDeviation', () => {
  it('相对基准单价计算偏离度（正=高于基准）', () => {
    expect(computeDeviation(1040, 850)).toBeCloseTo(22.35, 2);
    expect(computeDeviation(820, 850)).toBeCloseTo(-3.53, 2);
    expect(computeDeviation(850, 850)).toBe(0);
  });

  it('基准为 0 时返回 0（不产生 Infinity/NaN）', () => {
    expect(computeDeviation(100, 0)).toBe(0);
  });
});

describe('deviationLevel（超阈值高亮的判定依据）', () => {
  it('超过阈值 → over', () => {
    expect(deviationLevel(1040, camera)).toBe('over');
  });

  it('高于基准但未超阈值 → above', () => {
    expect(deviationLevel(875, camera)).toBe('above');
  });

  it('恰好等于阈值不算超（边界为「超过」）', () => {
    expect(deviationLevel(1000, camera)).toBe('above');
  });

  it('低于或等于基准 → below', () => {
    expect(deviationLevel(820, camera)).toBe('below');
    expect(deviationLevel(850, camera)).toBe('below');
  });
});

describe('reductionToThreshold（调价目标测算）', () => {
  it('超阈值：给出需下调的金额', () => {
    expect(reductionToThreshold(1040, camera)).toBe(40);
  });

  it('已在阈值内：返回 0', () => {
    expect(reductionToThreshold(875, camera)).toBe(0);
    expect(reductionToThreshold(1000, camera)).toBe(0);
  });
});

describe('sortQuotes / itemStats', () => {
  it('报价按单价升序', () => {
    const prices = sortQuotes(camera.quotes).map((q) => q.unitPrice);
    expect(prices).toEqual([820, 875, 1040]);
  });

  it('小结给出家数、最低/最高价与超阈值计数', () => {
    expect(itemStats(camera)).toEqual({
      quoteCount: 3,
      lowest: 820,
      highest: 1040,
      overThreshold: 1,
    });
  });

  it('空报价不炸（家数 0，价格 0）', () => {
    expect(itemStats({ ...camera, quotes: [] })).toEqual({
      quoteCount: 0,
      lowest: 0,
      highest: 0,
      overThreshold: 0,
    });
  });
});

describe('fmtDeviation', () => {
  it('带符号、一位小数', () => {
    expect(fmtDeviation(22.352)).toBe('+22.4%');
    expect(fmtDeviation(-3.5)).toBe('-3.5%');
    expect(fmtDeviation(0)).toBe('0.0%');
  });
});
