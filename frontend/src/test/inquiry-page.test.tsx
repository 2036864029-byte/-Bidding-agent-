import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { InquiryPage } from '@/pages/inquiry/InquiryPage';
import { setDataSource } from '@/services/api';

/* 智能询价页 —— 2026-09-16 改版后**唯一链路**：粘贴项目概况 → POST /inquiry/reference
 * → 金额区间（万元）+ 相似项目卡。原左栏对话控制台已移除，对话联动入口的横幅与
 * 预填仍保留（原问题预填到下方输入框）。
 *
 * 核心行为：
 * ① 首屏是项目概况参考面板（idle 引导），不预填编造的数据；
 * ② 从对话联动进入：显示来源横幅 + 原问题预填进输入框；
 * ③ 无联动参数时不显示横幅；
 * ④ 提交概况 → 金额区间卡与相似项目卡出现（面板自身的边界用例在
 *    reference-panel.test.tsx 覆盖，这里只验证整页链路能走通）。 */

beforeEach(() => setDataSource('reference', 'mock'));

function renderPage(entry = '/inquiry') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <InquiryPage />
    </MemoryRouter>
  );
}

describe('智能询价页', () => {
  it('首屏是项目概况参考面板（idle），不预填编造的数据', () => {
    renderPage();

    expect(screen.getByText('项目概况参考')).toBeInTheDocument();
    expect(screen.getByText(/查询后这里会显示相似中标项目的金额区间/)).toBeInTheDocument();
    // 无聊天派生残留：左栏已移除
    expect(screen.queryByText(/正在检索相似项目/)).toBeNull();
  });

  it('从对话联动进入：显示来源横幅 + 预填原问题到输入框', async () => {
    const q = '海康威视中标了哪些项目？';
    renderPage(`/inquiry?from=chat&q=${encodeURIComponent(q)}`);

    expect(await screen.findByText('来自对话')).toBeInTheDocument();
    expect(screen.getByText(`「${q}」`)).toBeInTheDocument();
    // 原问题被带进项目概况输入框（用户确认后点「查金额参考」）
    expect(screen.getByDisplayValue(q)).toBeInTheDocument();
  });

  it('无联动参数时不显示来源横幅', () => {
    renderPage();
    expect(screen.queryByText('来自对话')).toBeNull();
  });

  it('提交概况 → 金额区间卡与相似项目卡出现（整页链路）', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('项目概况文本'), {
      target: { value: '采购120台监控摄像头用于校园安防监控项目' },
    });
    fireEvent.click(screen.getByRole('button', { name: /查金额参考/ }));

    // mock fixture：min 42.5 / max 597.92 → 固定 2 位小数万元
    expect(await screen.findByText('42.50 万元 ~ 597.92 万元')).toBeInTheDocument();
    expect(screen.getByText('校园安防视频监控系统升级改造项目')).toBeInTheDocument();
  });
});
