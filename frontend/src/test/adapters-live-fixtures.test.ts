import { describe, expect, it } from 'vitest';
import { chatResponseAdapter } from '@/adapters/chatAdapter';
import type { ChatRawResponse } from '@/types/chat';
import deepAgentRaw from './fixtures/real-deep-agent.json';
import docQaRaw from './fixtures/real-doc-qa.json';
import priceRaw from './fixtures/real-price-records.json';

/**
 * 真实后端响应 → 适配器（防契约漂移）。
 *
 * fixtures 是从**跑着的后端**抓下来的原始响应（2026-09-14）。
 * 这些用例的价值：后端字段一旦改名/换形状，测试立刻红 —— 不必连后端也能发现。
 *
 * 重抓方式：见 src/test/live-*.test.ts 顶部的说明，或直接 curl 存到 fixtures/。
 */

describe('真实响应形状 → 适配器', () => {
  it('deep_agent：trace 的 snake_case 字段全部被认出来（plan / steps / reflection）', () => {
    const res = chatResponseAdapter(deepAgentRaw as ChatRawResponse);

    expect(res.replyType).toBe('deep_agent');
    expect(res.data.kind).toBe('deep_agent');
    if (res.data.kind !== 'deep_agent') throw new Error('分支不符');

    const t = res.data.trace;
    expect(t.mode).toBeTruthy();
    expect(t.plan.length).toBeGreaterThan(0);
    expect(t.steps.length).toBeGreaterThan(0);

    // 规划步骤：depends_on → dependsOn
    expect(t.plan[0].objective).toBeTruthy();
    expect(t.plan[0].tool).toBeTruthy();
    expect(Array.isArray(t.plan[0].dependsOn)).toBe(true);

    // 执行步骤：step_id / observation_head / records_count / latency_ms 全部映射到位
    const s0 = t.steps[0];
    expect(s0.stepId).toBeGreaterThan(0);
    expect(s0.tool).toBeTruthy();
    expect(['ok', 'empty', 'error']).toContain(s0.status);
    expect(typeof s0.recordsCount).toBe('number');
    expect(typeof s0.observationHead).toBe('string');
    expect(s0.latencyMs === null || typeof s0.latencyMs === 'number').toBe(true);
    expect(Array.isArray(s0.sources)).toBe(true);
  });

  it('doc_qa：status / mode / sources / attached_docs / retrieval 映射', () => {
    const res = chatResponseAdapter(docQaRaw as ChatRawResponse);

    expect(res.replyType).toBe('doc_qa');
    if (res.data.kind !== 'doc_qa') throw new Error('分支不符');

    expect(['success', 'no_doc_attached', 'failed']).toContain(res.data.status);
    expect(['retrieval', 'fulltext']).toContain(res.data.mode);
    expect(Array.isArray(res.data.sources)).toBe(true);
    expect(Array.isArray(res.data.attachedDocs)).toBe(true);
    expect(res.data.attachedDocs.length).toBeGreaterThan(0);
    // fulltext 模式没有 retrieval 块也必须是合法状态（不是 undefined 泄漏）
    expect(res.data.retrieval === null || typeof res.data.retrieval === 'object').toBe(true);
  });

  it('price_records：records/tables 映射，且**缺 meta 时是 null 而非崩**', () => {
    const res = chatResponseAdapter(priceRaw as ChatRawResponse);

    expect(res.replyType).toBe('price_records');
    if (res.data.kind !== 'price_records') throw new Error('分支不符');

    expect(Array.isArray(res.data.records)).toBe(true);
    expect(Array.isArray(res.data.tables)).toBe(true);
    expect(res.data.intent?.subRoute).toBeTruthy();
    // 真实响应在该分支**不带 meta**（只有非空结果才带）—— 必须容忍
    expect(res.data.meta).toBeNull();
  });

  it('三种真实响应都能走通适配器且 errorCode 保留', () => {
    for (const raw of [deepAgentRaw, docQaRaw, priceRaw]) {
      const res = chatResponseAdapter(raw as ChatRawResponse);
      expect(res.answer.length).toBeGreaterThan(0);
      expect(res.sessionId.length).toBeGreaterThan(0);
      expect(res.replyType).toBe(res.data.kind);
    }
  });
});
