import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { LandingLayout } from '@/components/layout/LandingLayout';
import { useChatWindowStore } from '@/store/chat-window';

/* 落地页四层结构 + 对话浮窗闭环（D1~D7）：
 * 导航/介绍带/三卡片/输入条 → 发送 → 浮窗 → 收起胶囊 → 恢复同一会话 */

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/chat']}>
      <LandingLayout />
    </MemoryRouter>
  );
}

describe('落地页四层结构', () => {
  beforeEach(() => {
    useChatWindowStore.setState({ status: 'idle', messages: [], input: '' });
  });

  it('渲染吸顶导航 + 介绍带 + 三卡片 + 输入条', () => {
    renderLanding();

    // 导航四链接 + 品牌
    expect(screen.getByRole('navigation')).toBeInTheDocument();
    for (const label of ['功能介绍', '常见问题', '移动端连接', '友情链接']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }

    // 介绍带（去掉 THREE CORE MODULES，H1 为中文）
    expect(screen.getByText('从询价、供应商到商机，一站式智能分析')).toBeInTheDocument();
    expect(screen.queryByText('THREE CORE MODULES')).toBeNull();

    // 三模块卡
    for (const title of ['智能询价', '供应商全景', '商机看板']) {
      expect(screen.getByRole('link', { name: new RegExp(title) })).toBeInTheDocument();
    }

    // 输入条
    expect(screen.getByPlaceholderText(/输入你的问题/)).toBeInTheDocument();
  });

  it('发送 → 浮窗打开（背景遮罩）→ × 收起为胶囊 → 点击恢复同一会话', () => {
    renderLanding();

    const input = screen.getByPlaceholderText(/输入你的问题/);
    fireEvent.change(input, { target: { value: '招标方式有哪些？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    // 浮窗打开：用户消息 + 占位回复可见；dock 输入条隐藏（输入移入浮窗）
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('招标方式有哪些？')).toBeInTheDocument();
    expect(screen.getByText(/已收到/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/输入你的问题/)).toBeNull();

    // 收起（非销毁）
    fireEvent.click(screen.getByRole('button', { name: '收起对话' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: /继续对话/ })).toBeInTheDocument();
    // dock 输入条回归
    expect(screen.getByPlaceholderText(/输入你的问题/)).toBeInTheDocument();

    // 恢复同一会话（消息不丢）
    fireEvent.click(screen.getByRole('button', { name: /继续对话/ }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('招标方式有哪些？')).toBeInTheDocument();
  });

  it('发送后浮窗持续保持打开，不会数秒后自动收起', async () => {
    renderLanding();

    fireEvent.change(screen.getByPlaceholderText(/输入你的问题/), {
      target: { value: '你好' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // 等待 3.5 秒（模拟用户观察期），浮窗必须仍在
    await new Promise((r) => setTimeout(r, 3500));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('你好')).toBeInTheDocument();
    expect(screen.getByText(/已收到/)).toBeInTheDocument();
  });
});
