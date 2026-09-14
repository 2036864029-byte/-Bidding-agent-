import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockChatFixtures } from '@/mock/mockChat';
import { api, setDataSource } from '@/services/api';
import { BackendError } from '@/services/backend';

/* 数据源开关（Phase 2 硬验收）：Mock ↔ 空实现可互切。
 * ① mock 态：三模块返回经适配器的 Domain；
 * ② api 态（后端未接入）：三模块抛明确 BackendError（空实现）；
 * ③ chat：mock 态取 fixture；api 态路由到 backend（fetch）并回填 Domain。 */

/** 最小 Response 假体（避免依赖 jsdom 的 Response 实现）。 */
const jsonResponse = (data: unknown, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, json: async () => data }) as unknown as Response;

describe('数据源开关 Mock ↔ API', () => {
  beforeEach(() => {
    (['kanban', 'inquiry', 'supplier', 'chat'] as const).forEach((m) => setDataSource(m, 'mock'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('默认 mock：三模块返回经适配器的 Domain 数据', async () => {
    expect((await api.kanban.getOverview()).summary.totalProjects).toBe(1243);
    expect((await api.inquiry.getSummary()).items.length).toBeGreaterThan(0);
    expect((await api.supplier.getOverview()).suppliers.length).toBeGreaterThan(0);
  });

  it('翻到 api 且后端未接入：三模块抛明确 BackendError（空实现）', async () => {
    setDataSource('kanban', 'api');
    await expect(api.kanban.getOverview()).rejects.toBeInstanceOf(BackendError);
    await expect(api.kanban.getOverview()).rejects.toThrow(/尚未接入/);

    setDataSource('inquiry', 'api');
    await expect(api.inquiry.getSummary()).rejects.toThrow(/尚未接入/);
  });

  it('chat mock 态：按 replyType 取 fixture', async () => {
    const res = await api.chat.send('随便问问', { replyType: 'deep_agent' });
    expect(res.replyType).toBe('deep_agent');
    expect(res.data.kind).toBe('deep_agent');
  });

  it('chat mock 态：无 replyType 时按关键词路由（价格类 → price_records）', async () => {
    const res = await api.chat.send('高清网络摄像机的历史中标均价大概多少？');
    expect(res.replyType).toBe('price_records');
    expect(res.data.kind).toBe('price_records');
  });

  it('chat mock 态：知识类问题落到 kb_answer', async () => {
    const res = await api.chat.send('招标方式有哪些？');
    expect(res.replyType).toBe('kb_answer');
  });

  it('chat 翻到 api：路由到 backend（POST /api/chat）并回填 Domain', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(mockChatFixtures.doc_qa));
    vi.stubGlobal('fetch', fetchMock);
    setDataSource('chat', 'api');

    const res = await api.chat.send('这个项目的投标保证金是多少？');
    expect(res.replyType).toBe('doc_qa');
    expect(res.data.kind).toBe('doc_qa');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat',
      expect.objectContaining({ method: 'POST' })
    );
  });
});
