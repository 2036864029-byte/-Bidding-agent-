import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockChatFixtures } from '@/mock/mockChat';
import { mockKanbanRaw } from '@/mock/mockKanban';
import { mockReferenceRaw } from '@/mock/mockReference';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import { api, setDataSource } from '@/services/api';

/* 数据源开关（Phase 2 硬验收）：Mock ↔ 空实现可互切。
 * ① mock 态：模块返回经适配器的 Domain；
 * ② api 态（后端未接入）：模块抛明确 BackendError（空实现）；
 * ③ chat：mock 态取 fixture；api 态路由到 backend（fetch）并回填 Domain。
 * 注：智能询价没有数据源开关 —— 它的报价参考是 chat 结果的派生视图。 */

/** 最小 Response 假体（避免依赖 jsdom 的 Response 实现）。 */
const jsonResponse = (data: unknown, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, json: async () => data }) as unknown as Response;

describe('数据源开关 Mock ↔ API', () => {
  beforeEach(() => {
    (['kanban', 'supplier', 'chat', 'reference'] as const).forEach((m) => setDataSource(m, 'mock'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('mock 态：模块返回经适配器的 Domain 数据', async () => {
    expect((await api.kanban.getOverview()).summary.totalProjects).toBe(18497);
    expect((await api.supplier.getOverview()).suppliers.length).toBeGreaterThan(0);
  });

  it('kanban 翻到 api：走真实 /kanban/overview 并回填 Domain', async () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(mockKanbanRaw));
    vi.stubGlobal('fetch', fetchMock);
    setDataSource('kanban', 'api');

    const r = await api.kanban.getOverview();
    expect(r.summary.totalProjects).toBe(18497);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/kanban/overview');
  });

  it('supplier 翻到 api：走真实 /suppliers/overview 并回填 Domain', async () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(mockSupplierRaw));
    vi.stubGlobal('fetch', fetchMock);
    setDataSource('supplier', 'api');

    const r = await api.supplier.getOverview();
    expect(r.suppliers.length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/suppliers/overview');
  });

  it('supplier 翻到 api：筛选条件拼进 query（服务端筛选）', async () => {
    const fetchMock = vi.fn(async (_url: string) => jsonResponse(mockSupplierRaw));
    vi.stubGlobal('fetch', fetchMock);
    setDataSource('supplier', 'api');

    await api.supplier.getOverview({ province: '安徽省', industry: '批发业', keyword: '科' });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('province=');
    expect(url).toContain('industry=');
    expect(url).toContain('keyword=');
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

  it('reference mock 态：返回经适配器的 Domain（金额万元）', async () => {
    const r = await api.reference.fetch('采购120台监控摄像头用于校园安防监控项目');
    expect(r.total).toBe(4);
    expect(r.stats?.maxWan).toBe(597.92);
    expect(r.projects[0].sourceUrl).toContain('ccgp.gov.cn');
  });

  it('reference 翻到 api：POST /api/inquiry/reference 并回填 Domain', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse(mockReferenceRaw)
    );
    vi.stubGlobal('fetch', fetchMock);
    setDataSource('reference', 'api');

    const r = await api.reference.fetch('采购120台监控摄像头用于校园安防监控项目', { topK: 5 });
    expect(r.total).toBe(4);
    expect(r.projects[0].winningAmountWan).toBe(133.94);
    // 请求体：text + top_k 组装正确
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/inquiry/reference');
    expect(JSON.parse(String(init?.body))).toEqual({
      text: '采购120台监控摄像头用于校园安防监控项目',
      top_k: 5,
    });
  });
});
