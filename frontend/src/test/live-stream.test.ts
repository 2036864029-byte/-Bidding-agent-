import { describe, expect, it } from 'vitest';
import { api, setDataSource } from '@/services/api';
import type { ChatResponse } from '@/types/chat';

/**
 * 真实后端联调（SSE）—— **默认跳过**。
 * 跑法：起后端，然后 `VITE_API_BASE=http://127.0.0.1:8000 npx vitest run src/test/live-stream.test.ts`
 *
 * 为什么放这里而不是删掉：这条用例验的是**客户端代码本身**（不是 curl）能否对上真实 SSE 契约，
 * 光靠 mock 永远发现不了契约漂移。
 */
const LIVE = Boolean(import.meta.env?.VITE_API_BASE);

describe.skipIf(!LIVE)('真实后端 · 流式链路', () => {
  it('session → result：事件序与终态归一', async () => {
    setDataSource('chat', 'api');

    const events: string[] = [];
    let tokens = 0;
    let result: ChatResponse | null = null;

    await api.chat.stream(
      '你好，请用一句话介绍你自己',
      {},
      {
        onSession: () => events.push('session'),
        onToken: () => {
          tokens += 1;
        },
        onResult: (r) => {
          result = r;
        },
      }
    );

    expect(events).toContain('session');
    expect(result).not.toBeNull();
    // 终态是 Domain 形状（replyType / data.kind 已归一）
    expect(result!.replyType).toBe(result!.data.kind);
    expect(result!.answer.length).toBeGreaterThan(0);
    // 注：闲聊/价格类分支**本来就不产 token**（后端只在 kb_answer / doc_qa / deep_agent
    // 的生成节点调用 emit_token），所以这里不断言 tokens > 0。
    expect(tokens).toBeGreaterThanOrEqual(0);
  }, 90_000);

  it('知识问答：流式拿回带引用的回答（需要向量检索依赖在线）', async () => {
    setDataSource('chat', 'api');

    let result: ChatResponse | null = null;
    // 用一条**没被缓存过**的问题，避免命中旧缓存掩盖真实链路
    await api.chat.stream(
      `评标委员会的人数要求是什么？${Date.now()}`,
      {},
      { onResult: (r) => (result = r) }
    );

    expect(result).not.toBeNull();
    // 依赖不可用时后端会降级成 biz_1003（检索为空）—— 那种情况这条用例就该红，
    // 因为它恰恰是「知识问答端到端可用」的验收。
    expect(result!.errorCode).toBeNull();
    expect(result!.replyType).toBe('kb_answer');
    expect(result!.citations.length).toBeGreaterThan(0);
    expect(result!.answer.length).toBeGreaterThan(20);
  }, 120_000);
});
