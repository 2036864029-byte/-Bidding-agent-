import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FeishuBindDialog } from '@/components/chat/FeishuBindDialog';
import { LandingLayout } from '@/components/layout/LandingLayout';
import { useChatWindowStore } from '@/store/chat-window';

/* 并行线 A「连接飞书」弹窗：两种情况渲染 + 回调落地。 */

describe('FeishuBindDialog', () => {
  beforeEach(() => {
    useChatWindowStore.setState({ feishuPrompt: null, sessionId: 's1' });
  });
  afterEach(() => vi.restoreAllMocks());

  it('null → 不渲染', () => {
    render(<FeishuBindDialog />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('情况1（未绑定）→ 引导文案 + 「连接飞书」按钮', () => {
    useChatWindowStore.setState({ feishuPrompt: 'unbound' });
    render(<FeishuBindDialog />);
    expect(screen.getByText(/您可以连接飞书/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /连接飞书/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '暂不' })).toBeInTheDocument();
  });

  it('情况2（已绑定）→ 已连接文案 + 「知道了」', () => {
    useChatWindowStore.setState({ feishuPrompt: 'bound' });
    render(<FeishuBindDialog />);
    expect(screen.getByText(/您已经连接了飞书/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知道了' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '暂不' })).not.toBeInTheDocument();
  });

  it('点「连接飞书」→ 触发 connectFeishu', () => {
    useChatWindowStore.setState({ feishuPrompt: 'unbound' });
    const spy = vi.spyOn(useChatWindowStore.getState(), 'connectFeishu');
    render(<FeishuBindDialog />);
    fireEvent.click(screen.getByRole('button', { name: /连接飞书/ }));
    expect(spy).toHaveBeenCalled();
  });

  it('点「知道了」→ 触发 dismissFeishuPrompt', () => {
    useChatWindowStore.setState({ feishuPrompt: 'bound' });
    const spy = vi.spyOn(useChatWindowStore.getState(), 'dismissFeishuPrompt');
    render(<FeishuBindDialog />);
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(spy).toHaveBeenCalled();
  });
});

describe('LandingLayout：授权回调落地', () => {
  beforeEach(() => {
    useChatWindowStore.setState({
      status: 'idle',
      messages: [],
      input: '',
      files: [],
      busy: false,
      sessionId: 's1',
      notice: null,
      feishuPrompt: null,
    });
  });

  it('URL 带 ?feishu=bound → 弹「已连接」并清理 URL', () => {
    window.history.replaceState({}, '', '/chat?feishu=bound');
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <LandingLayout />
      </MemoryRouter>
    );
    expect(screen.getByText(/您已经连接了飞书/)).toBeInTheDocument();
    expect(window.location.search).toBe('');
  });

  it('URL 不带 feishu → 不弹', () => {
    window.history.replaceState({}, '', '/chat');
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <LandingLayout />
      </MemoryRouter>
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
