import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { chatResponseAdapter } from '@/adapters/chatAdapter';
import { ReplyRenderer } from '@/components/chat/renderers/ReplyRenderer';
import { mockChatFixtures } from '@/mock/mockChat';
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
];

describe('ReplyRenderer 注册表', () => {
  it('7 种 reply_type 都能渲染出正文（无一落空）', () => {
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

  it('price_records：渲染中标记录表 + 询价模块联动入口', () => {
    renderReply('price_records');
    expect(screen.getByText('智慧园区安防系统集成项目')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /在询价模块查看详情/ })).toHaveAttribute(
      'href',
      '/inquiry'
    );
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

  it('流式过程中（还没有 result）按纯文本渲染，不报错', () => {
    render(
      <MemoryRouter>
        <ReplyRenderer message={{ id: 's', role: 'agent', content: '正在生成中的片段…', pending: true }} />
      </MemoryRouter>
    );
    expect(screen.getByText('正在生成中的片段…')).toBeInTheDocument();
  });
});
