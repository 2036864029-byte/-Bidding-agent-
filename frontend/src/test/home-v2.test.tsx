import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { LandingLayout } from '@/components/layout/LandingLayout';
import { ModulesPage } from '@/pages/modules/ModulesPage';
import { useChatWindowStore } from '@/store/chat-window';

/* 首页 `/chat` 与三模块页 `/modules` 的职责划分（2026-11-24）：
 *   /chat    = 首屏（标题 + 对话示例卡）+ 一条对话框
 *   /modules = 三张模块卡 + 一条对话框；导航「开始」的落点
 * 两页各自带对话框，共用同一个 chat store；换成独立路由后浏览器后退/直链均可用。 */

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="loc">{`${loc.pathname}${loc.hash}`}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/chat" element={<LandingLayout />} />
        <Route path="/modules" element={<ModulesPage />} />
      </Routes>
    </MemoryRouter>
  );
}

const MODULE_TITLES = ['智能询价', '供应商全景', '商机看板'];

describe('首页 /chat', () => {
  beforeEach(() => {
    useChatWindowStore.setState({
      status: 'idle',
      messages: [],
      input: '',
      files: [],
      busy: false,
      sessionId: null,
      notice: null,
    });
  });

  it('只有首屏（无模块卡、无常驻输入行），底部是一个居中的开始提问按钮', () => {
    renderAt('/chat');

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('招投标的问题');
    // 三模块卡已移出首页
    for (const t of MODULE_TITLES) {
      expect(screen.queryByText(t)).toBeNull();
    }
    // 首页不再有输入行：输入移入浮窗
    expect(screen.queryByPlaceholderText(/输入你的问题/)).toBeNull();
    expect(screen.getByRole('button', { name: /开始提问/ })).toBeInTheDocument();
  });

  it('点导航 home 图标 → 回 /chat（导航条已无「开始」按钮）', () => {
    renderAt('/chat');
    // 已改为 home 图标
    expect(screen.queryByRole('button', { name: '开始' })).toBeNull();
    expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
  });

  it('底部「开始提问」→ 跳转 /modules 并渲染三张模块卡', () => {
    renderAt('/chat');

    fireEvent.click(screen.getByRole('button', { name: /开始提问/ }));

    expect(screen.getByTestId('loc')).toHaveTextContent('/modules');
    // 是路由跳转，不弹浮窗
    expect(screen.queryByRole('dialog')).toBeNull();
    for (const t of MODULE_TITLES) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    // 三模块页仍带一条常用对话框
    expect(screen.getAllByPlaceholderText(/输入你的问题/)).toHaveLength(1);
  });
});

describe('三模块页 /modules', () => {
  beforeEach(() => {
    useChatWindowStore.setState({
      status: 'idle',
      messages: [],
      input: '',
      files: [],
      busy: false,
      sessionId: null,
      notice: null,
    });
  });

  it('可直链进入：三张模块卡 + 一条对话框', () => {
    renderAt('/modules');

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('一站式智能分析');
    for (const t of MODULE_TITLES) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getAllByPlaceholderText(/输入你的问题/)).toHaveLength(1);
    // 是独立的模块卡链接，可进入各模块详情页
    expect(screen.getByRole('link', { name: /智能询价/ })).toHaveAttribute('href', '/inquiry');
    expect(screen.getByRole('link', { name: /供应商全景/ })).toHaveAttribute('href', '/supplier');
    expect(screen.getByRole('link', { name: /商机看板/ })).toHaveAttribute('href', '/kanban');
  });

  it('在 /modules 点 home 图标 → 回到 /chat', () => {
    renderAt('/modules');
    fireEvent.click(screen.getByRole('button', { name: '返回首页' }));
    expect(screen.getByTestId('loc')).toHaveTextContent('/chat');
  });
});

describe('已移除的控件（防回潮）', () => {
  beforeEach(() => {
    useChatWindowStore.setState({
      status: 'idle',
      messages: [],
      input: '',
      files: [],
      busy: false,
      sessionId: null,
      notice: null,
    });
  });

  it('首屏不再有「查看三个模块」按钮', () => {
    renderAt('/chat');
    expect(screen.queryByRole('button', { name: '查看三个模块' })).toBeNull();
  });

  it('顶部导航第一项是 home 图标，且没有「开始/开始提问」文字按钮', () => {
    renderAt('/chat');
    const header = document.querySelector('header');
    expect(header).not.toBeNull();
    const firstBtn = header?.querySelector('button');
    expect(firstBtn).toHaveAttribute('aria-label', '返回首页');
    expect(firstBtn?.textContent).toBe('');
    expect(header?.querySelector('[aria-label="开始提问"]')).toBeNull();
  });
});
