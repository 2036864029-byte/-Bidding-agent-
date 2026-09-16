import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { chatResponseAdapter } from '@/adapters/chatAdapter';
import { deriveColumns, resolveColumns } from '@/components/chat/PriceRecordsTable';
import { ReplyRenderer } from '@/components/chat/renderers/ReplyRenderer';
import { mockChatFixtures } from '@/mock/mockChat';
import { useChatWindowStore } from '@/store/chat-window';
import type { ChatMessage, ReplyType } from '@/types/chat';

/* reply_type 渲染注册表：7 种分支逐一验证渲染结果。
 * 这是 Phase 4 的核心契约 —— data 是通用容器，前端必须按 reply_type 分支渲染，
 * 漏掉任何一个分支都会让「agent 成功作答」被渲染成空白。 */

function messageOf(type: ReplyType): ChatMessage {
  const res = chatResponseAdapter(mockChatFixtures[type]);
  return {
    id: `m-${type}`,
    role: 'agent',
    content: res.answer,
    replyType: res.replyType,
    citations: res.citations,
    data: res.data,
    errorCode: res.errorCode,
  };
}

function renderReply(type: ReplyType) {
  return render(
    <MemoryRouter>
      <ReplyRenderer message={messageOf(type)} />
    </MemoryRouter>
  );
}

const ALL_TYPES: ReplyType[] = [
  'kb_answer',
  'price_records',
  'doc_qa',
  'deep_agent',
  'chat',
  'safety',
  'fallback',
  'clarify',
];

describe('ReplyRenderer 注册表', () => {
  it('8 种 reply_type 都能渲染出正文（无一落空）', () => {
    for (const t of ALL_TYPES) {
      const { unmount } = renderReply(t);
      expect(document.body.textContent?.trim().length ?? 0).toBeGreaterThan(20);
      unmount();
    }
  });

  it('kb_answer：正文 + 引用卡片，点【来源N】展开原文', () => {
    renderReply('kb_answer');
    expect(screen.getByText('引用来源 · 2')).toBeInTheDocument();

    // 正文里的标记是可点击控件（不是死文字）
    fireEvent.click(screen.getByRole('button', { name: '【来源1】' }));
    const card = screen.getByRole('button', { name: /【来源1】.*招标投标法/ });
    expect(card).toHaveAttribute('aria-expanded', 'true');
    // 展开后给出原文片段
    expect(screen.getByText(/招标分为公开招标和邀请招标/)).toBeInTheDocument();
  });

  it('price_records：渲染中标记录表 + 询价模块联动入口（带上原始提问）', () => {
    const res = chatResponseAdapter(mockChatFixtures.price_records);
    render(
      <MemoryRouter>
        <ReplyRenderer
          message={{
            id: 'pr',
            role: 'agent',
            content: res.answer,
            replyType: res.replyType,
            data: res.data,
            sourceQuestion: '海康威视中标了哪些项目？',
          }}
        />
      </MemoryRouter>
    );
    expect(screen.getByText('智慧园区安防系统集成项目')).toBeInTheDocument();
    // 表头来自**服务端下发的 columns**（前端不再硬编码「机器名 ↔ 中文」）
    expect(screen.getByRole('columnheader', { name: '项目名称' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '中标金额' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '中标供应商' })).toBeInTheDocument();

    // 联动跳转必须把上下文带过去（不丢）
    const link = screen.getByRole('link', { name: /在询价模块查看详情/ });
    expect(link.getAttribute('href')).toContain('/inquiry?from=chat');
    expect(link.getAttribute('href')).toContain(encodeURIComponent('海康威视中标了哪些项目？'));
  });

  it('price_records（供应商类）：跳供应商模块并按公司名筛选（不再绕询价）', () => {
    const res = chatResponseAdapter({
      ...mockChatFixtures.price_records,
      answer: '为您推荐 2 家安徽省的软件信息服务商：\n\n① 安徽海纳信息科技有限公司……',
      data: {
        ...(mockChatFixtures.price_records.data as Record<string, unknown>),
        records: [
          { company_name: '安徽海纳信息科技有限公司', industry: '软件和信息技术服务业', province: '安徽省' },
          { company_name: '合肥某某科技有限公司', industry: '软件和信息技术服务业', province: '安徽省' },
        ],
        intent: { sub_route: 'company_query', query_type: 'supplier_recommend', semantic_keywords: ['软件'] },
        columns: [],
      },
    });
    render(
      <MemoryRouter>
        <ReplyRenderer
          message={{
            id: 'sup',
            role: 'agent',
            content: res.answer,
            replyType: res.replyType,
            data: res.data,
            sourceQuestion: '推荐几个做安防的供应商',
          }}
        />
      </MemoryRouter>
    );

    // 供应商类结果 → 直跳供应商模块（不是询价），并带上**首条记录的公司名**作为搜索词
    const link = screen.getByRole('link', { name: /在供应商模块查看详情/ });
    expect(link.getAttribute('href')).toContain('/supplier?from=chat');
    expect(link.getAttribute('href')).toContain(encodeURIComponent('安徽海纳信息科技有限公司'));
  });

  it('doc_qa：来源列表 + retrieval.notes 显式兜底提示', () => {
    renderReply('doc_qa');
    expect(screen.getByText(/检索到的段落 · 2/)).toBeInTheDocument();
    // 两个来源同属一份文档 —— 用 getAll 避免"多元素匹配"的脆弱断言
    expect(screen.getAllByText(/智慧园区智慧化改造项目招标文件\.pdf\.md/).length).toBe(2);
  });

  it('doc_qa：后端给了 notes 就必须展示给用户（低相关度兜底）', () => {
    const res = chatResponseAdapter({
      ...mockChatFixtures.doc_qa,
      data: {
        ...(mockChatFixtures.doc_qa.data as Record<string, unknown>),
        retrieval: {
          mode: 'retrieval',
          blocks: 3,
          truncated: true,
          notes: ['⚠️ 在《X》中未检索到与问题高度相关的段落（最高相关度 0.42），以下回答仅供参考。'],
        },
      },
    });
    render(
      <MemoryRouter>
        <ReplyRenderer
          message={{ id: 'x', role: 'agent', content: res.answer, replyType: res.replyType, data: res.data }}
        />
      </MemoryRouter>
    );
    expect(screen.getByText(/未检索到与问题高度相关的段落/)).toBeInTheDocument();
  });

  it('deep_agent：思维链折叠面板默认收起，展开后给出规划/执行/反思', () => {
    renderReply('deep_agent');

    const toggle = screen.getByRole('button', { name: /思考过程/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('规划')).toBeNull();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('规划')).toBeInTheDocument();
    expect(screen.getByText('执行')).toBeInTheDocument();
    expect(screen.getByText('反思')).toBeInTheDocument();
    // 每步的来源可核验
    expect(screen.getByText(/智慧园区智慧化改造项目招标文件\.pdf｜第三章/)).toBeInTheDocument();
    // 步数汇总
    expect(within(toggle).getByText(/2 步规划 · 2 步执行/)).toBeInTheDocument();
  });

  it('safety：给出明确的安全边界标识，不伪装成正常回答', () => {
    renderReply('safety');
    expect(screen.getByText('安全边界提示')).toBeInTheDocument();
  });

  it('clarify：引导文案 + 候选 chips，点击候选发起新一轮提问', () => {
    const submitSpy = vi.fn();
    useChatWindowStore.setState({ submitText: submitSpy });
    renderReply('clarify');

    expect(screen.getByText('您是想问以下哪个？')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '公开招标和邀请招标的区别' }));
    expect(submitSpy).toHaveBeenCalledWith('公开招标和邀请招标的区别');
  });

  it('流式过程中（还没有 result）按纯文本渲染，不报错', () => {
    render(
      <MemoryRouter>
        <ReplyRenderer message={{ id: 's', role: 'agent', content: '正在生成中的片段…', pending: true }} />
      </MemoryRouter>
    );
    expect(screen.getByText('正在生成中的片段…')).toBeInTheDocument();
  });
});

/* records 是通用容器，列集随子路由变化 —— 表格必须自适应（写死列会让企业查询整表都是「—」）。 */

describe('PriceRecordsTable 列自适应', () => {
  it('招投标记录 → 项目列', () => {
    const cols = deriveColumns([
      { project_name: '某项目', purchaser: '某单位', winning_amount: '100', winning_date: '2026-01-01' },
    ]);
    expect(cols).toEqual(['project_name', 'purchaser', 'winning_amount', 'winning_date']);
  });

  it('企业记录 → 企业列（不再是一排「—」）', () => {
    const cols = deriveColumns([
      { company_name: '某公司', credit_code: '9133...', legal_person: '张三', registered_capital: '1000万' },
    ]);
    expect(cols).toContain('company_name');
    expect(cols).toContain('legal_person');
    expect(cols).not.toContain('project_name');
  });

  it('整列为空的列被丢掉', () => {
    const cols = deriveColumns([{ project_name: '甲', purchaser: '', winning_amount: null }]);
    expect(cols).toEqual(['project_name']);
  });

  it('列数封顶，避免变成字段倾倒', () => {
    const wide: Record<string, string> = {};
    for (let i = 0; i < 20; i += 1) wide[`f${i}`] = `v${i}`;
    expect(deriveColumns([wide]).length).toBeLessThanOrEqual(6);
  });

  it('空记录不产生列（组件据此不渲染表格）', () => {
    expect(deriveColumns([])).toEqual([]);
  });
});

/* 服务端下发 columns 后，前端不再需要自己硬编码「机器名 ↔ 中文表头」。 */

describe('resolveColumns 优先用服务端列定义', () => {
  const records = [
    { project_name: '某项目', winning_amount: '100', project_number: 'ZB-1' },
  ];

  it('服务端给了 columns：按它的顺序与表头走（覆盖本地启发式的排序）', () => {
    const cols = resolveColumns(records, [
      { key: 'project_number', label: '项目编号' },
      { key: 'project_name', label: '项目名称' },
      { key: 'winning_amount', label: '中标金额' },
    ]);
    expect(cols).toEqual([
      { key: 'project_number', label: '项目编号' },
      { key: 'project_name', label: '项目名称' },
      { key: 'winning_amount', label: '中标金额' },
    ]);
  });

  it('服务端标签为空时回落本地映射（不至于显示成裸机器名）', () => {
    const cols = resolveColumns(records, [{ key: 'project_name', label: '' }]);
    expect(cols).toEqual([{ key: 'project_name', label: '项目名称' }]);
  });

  it('服务端列与数据完全对不上 → 回落启发式', () => {
    const cols = resolveColumns(records, [{ key: 'not_in_data', label: '无关列' }]);
    expect(cols.map((c) => c.key)).toEqual(['project_name', 'project_number', 'winning_amount']);
  });

  it('旧后端没给 columns → 启发式 + 本地表头', () => {
    const cols = resolveColumns(records);
    expect(cols[0]).toEqual({ key: 'project_name', label: '项目名称' });
  });

  it('仍然封顶 6 列', () => {
    const wide: Record<string, string> = {};
    for (let i = 0; i < 20; i += 1) wide[`f${i}`] = `v${i}`;
    const server = Array.from({ length: 20 }, (_, i) => ({ key: `f${i}`, label: `列${i}` }));
    expect(resolveColumns([wide], server).length).toBeLessThanOrEqual(6);
  });
});
