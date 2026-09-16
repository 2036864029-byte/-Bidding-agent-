import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { InquiryPage } from '@/pages/inquiry/InquiryPage';
import { setDataSource } from '@/services/api';

/* 智能询价页集成 —— 本版的核心行为是**左右联动**：
 * ① 首屏是项目概况参考面板（独立端点链路）+ 无聊天派生的参考（不预填编造）；
 * ② 左侧提问 → 右栏实时算出中标价区间与相似项目清单（聊天派生链路）；
 * ③ 问非价格类问题时保留上一次参考（不清空）；
 * ④ 对话模块联动入口的横幅与预填仍工作。 */

beforeEach(() => setDataSource('reference', 'mock'));

function renderPage(entry = '/inquiry') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <InquiryPage />
    </MemoryRouter>
  );
}

/** 提问并等结果回来（mock fixture 同步返回，但仍走一次异步）。 */
async function ask(question: string) {
  const input = await screen.findByPlaceholderText(/例如：采购/);
  fireEvent.change(input, { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: '发送' }));
}

describe('智能询价页', () => {
  it('首屏是项目概况参考面板（idle），不预填编造的参考', () => {
    renderPage();

    // 独立端点链路的面板在场（idle 引导）
    expect(screen.getByText('项目概况参考')).toBeInTheDocument();
    expect(screen.getByText(/查询后这里会显示相似中标项目的金额区间/)).toBeInTheDocument();
    // 聊天派生链路无结果：不预填编造的报价参考
    expect(screen.queryByText('相似项目清单')).toBeNull();
    expect(screen.queryByText('金额参考')).toBeNull();
  });

  it('左侧提问价格类问题 → 右栏算出区间与相似项目清单（左右联动）', async () => {
    renderPage();
    await ask('摄像机的历史中标均价大概多少？');

    // 区间卡：fixture 三笔金额 486/512/288 万 → 288.00 ~ 512.00 万元
    expect(await screen.findByText('288.00 ~ 512.00 万元')).toBeInTheDocument();
    expect(screen.getByText('3 个相似项目')).toBeInTheDocument();

    // 参考卡标题取提问本身（提问同时出现在左栏气泡里，故按卡片作用域取）
    const card = screen.getByText('报价参考').closest('section') as HTMLElement;
    expect(within(card).getByText('摄像机的历史中标均价大概多少？')).toBeInTheDocument();
    // 空态已被真实结果取代
    expect(screen.queryByText('还没有报价参考')).toBeNull();

    // 相似项目清单（右栏）：项目名 + 中标供应商副行 + 地区 + 类别 + 金额 + 日期
    const list = screen.getByText('相似项目清单').closest('section') as HTMLElement;
    expect(within(list).getByText('智慧园区安防系统集成项目')).toBeInTheDocument();
    expect(within(list).getByText('486.00 万元')).toBeInTheDocument();
    expect(within(list).getByText('2026-06-18')).toBeInTheDocument();
    // fixture 三条：信息化设备 ×2、广东省 ×2，中标供应商是同一家 ×3
    expect(within(list).getAllByText('信息化设备')).toHaveLength(2);
    expect(within(list).getAllByText('广东省')).toHaveLength(2);
    expect(within(list).getAllByText('杭州海康威视数字技术股份有限公司')).toHaveLength(3);
  });

  it('问非价格类问题时不覆盖已有参考（右栏不清空）', async () => {
    renderPage();
    await ask('摄像机的历史中标均价大概多少？');
    expect(await screen.findByText('288.00 ~ 512.00 万元')).toBeInTheDocument();

    // 换一个知识类问题 → kb_answer，右栏应保留上一次的价格参考
    await ask('招标方式有哪些？');
    expect(await screen.findByText('288.00 ~ 512.00 万元')).toBeInTheDocument();
  });

  it('从对话联动进入：显示来源横幅 + 预填原问题（上下文不丢）', async () => {
    const q = '海康威视中标了哪些项目？';
    renderPage(`/inquiry?from=chat&q=${encodeURIComponent(q)}`);

    expect(await screen.findByText('来自对话')).toBeInTheDocument();
    expect(screen.getByText(`「${q}」`)).toBeInTheDocument();
    // 原问题被带进项目概况区（用户可直接发送继续）
    expect(screen.getByDisplayValue(q)).toBeInTheDocument();
  });

  it('无联动参数时不显示来源横幅', async () => {
    renderPage();
    expect(screen.queryByText('来自对话')).toBeNull();
  });

  it('上传入口在 mock 态可点（预校验通过后产生 chip）', async () => {
    renderPage();

    const picker = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(picker).toBeTruthy();
    expect(screen.getByRole('button', { name: '上传文件' })).toBeEnabled();

    const file = new File(['x'], '招标文件.pdf', { type: 'application/pdf' });
    fireEvent.change(picker, { target: { files: [file] } });

    expect(await screen.findByText('招标文件.pdf')).toBeInTheDocument();
  });

  it('上传预校验不通过时不产生 chip，只给提示', async () => {
    renderPage();

    const picker = document.querySelector('input[type="file"]') as HTMLInputElement;
    const bad = new File(['x'], '病毒.exe', { type: 'application/x-msdownload' });
    fireEvent.change(picker, { target: { files: [bad] } });

    expect(await screen.findByRole('alert')).toHaveTextContent('不支持的文件类型');
    expect(screen.queryByText('病毒.exe')).toBeNull();
  });
});
