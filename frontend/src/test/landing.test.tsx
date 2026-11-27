import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { LandingLayout } from '@/components/layout/LandingLayout';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { useChatWindowStore } from '@/store/chat-window';

/* 首页（/chat）与模块页外壳的职责（2026-11-24）：
 *
 *   /chat    首屏文案 + **一个**底部居中的「开始提问」按钮 → 点击进入 /modules
 *   /modules 三模块页（自带对话框）
 *   模块详情页 ModulePageLayout：吸底对话框 + 浮窗，对话闭环在这里验证
 *
 * 首页自本轮起不再承载输入行；浮窗只能从模块页的 dock 打开 ——
 * 所以「发送 / 收起 / 恢复」的闭环测试迁到 ModulePageLayout 上。 */

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="loc">{loc.pathname}</span>;
}

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/chat']}>
      <LocationProbe />
      <Routes>
        <Route path="/chat" element={<LandingLayout />} />
        <Route path="/modules" element={<div>modules-page</div>} />
      </Routes>
    </MemoryRouter>
  );
}

const resetStore = () => {
  useChatWindowStore.setState({
    status: 'idle',
    messages: [],
    input: '',
    files: [],
    busy: false,
    sessionId: null,
    notice: null,
  });
};

describe('首页结构', () => {
  beforeEach(resetStore);

  it('渲染导航 + 首屏标题 + 一个「开始提问」按钮（无常驻输入行）', () => {
    renderLanding();

    expect(screen.getByRole('navigation')).toBeInTheDocument();
    // 导航条第一项是 home 图标（不再是「开始」按钮）
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始' })).toBeNull();
    for (const label of ['功能介绍', '常见问题', '移动端连接', '友情链接']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('招投标的问题');

    // 只有一个主按钮（不再叠加第二个），且首页没有输入行
    expect(screen.getAllByRole('button', { name: /开始提问/ })).toHaveLength(1);
    expect(screen.queryByPlaceholderText(/追问，或输入新问题/)).toBeNull();
  });

  it('点「开始提问」→ 跳转 /modules', () => {
    renderLanding();
    fireEvent.click(screen.getByRole('button', { name: /开始提问/ }));
    expect(screen.getByTestId('loc')).toHaveTextContent('/modules');
  });
});

describe('模块页外壳：吸底对话框 + 浮窗闭环', () => {
  beforeEach(resetStore);

  const renderModuleShell = () =>
    render(
      <MemoryRouter initialEntries={['/kanban']}>
        <ModulePageLayout name="商机看板" eyebrow="OPPORTUNITY KANBAN" description="测试用模块页">
          <div>content</div>
        </ModulePageLayout>
      </MemoryRouter>
    );

  it('输入 → 浮窗打开 → × 收起 → 再次打开仍是同一会话', async () => {
    renderModuleShell();

    const input = screen.getByPlaceholderText(/输入你的问题/);
    fireEvent.change(input, { target: { value: '招标方式有哪些？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText('招标方式有哪些？')).toBeInTheDocument();

    // 流式回答到达（mock 按片吐 token，终态 result 覆盖）
    expect((await screen.findAllByText(/招标投标法/)).length).toBeGreaterThan(0);

    // 收起（非销毁）
    fireEvent.click(screen.getByRole('button', { name: '收起对话' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: /继续对话/ })).toBeInTheDocument();

    // 恢复同一会话（消息不丢）
    fireEvent.click(screen.getByRole('button', { name: /继续对话/ }));
    expect(within(screen.getByRole('dialog')).getByText('招标方式有哪些？')).toBeInTheDocument();
  });

  it('发送后浮窗持续保持打开，不会数秒后自动收起', async () => {
    renderModuleShell();

    fireEvent.change(screen.getByPlaceholderText(/输入你的问题/), {
      target: { value: '你好' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await new Promise((r) => setTimeout(r, 3500));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('你好')).toBeInTheDocument();
    expect(screen.getAllByText(/招标投标法/).length).toBeGreaterThan(0);
  });
});
