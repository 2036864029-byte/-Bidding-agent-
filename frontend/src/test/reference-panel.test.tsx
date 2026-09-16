import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectReferencePanel } from '@/components/inquiry/ProjectReferencePanel';
import { api, setDataSource } from '@/services/api';

/* 项目概况参考面板（POST /inquiry/reference 独立链路）。
 *
 * 数据源固定在 mock：本文件测的是**面板交互与渲染**，真实端点由 api-switch.test.ts 覆盖。
 * 核心行为：
 * ① <10 字禁用提交；
 * ② 提交后渲染金额区间卡（万元 2 位小数）+ 相似项目卡 + 查看原文外链 + 产品明细折叠；
 * ③ 结果为空 → 空态提示。 */

beforeEach(() => {
  setDataSource('reference', 'mock');
  vi.restoreAllMocks();
});

const VALID_TEXT = '采购120台监控摄像头用于校园安防监控项目';

async function submit(text: string) {
  fireEvent.change(screen.getByLabelText('项目概况文本'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /查金额参考/ }));
}

describe('项目概况参考面板', () => {
  it('idle：提示至少 10 字，按钮禁用', () => {
    render(<ProjectReferencePanel />);
    expect(screen.getByText(/至少 10 字/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查金额参考/ })).toBeDisabled();
    expect(screen.getByText(/查询后这里会显示/)).toBeInTheDocument();
  });

  it('initialText 预填输入框（对话联动入口用）', () => {
    render(<ProjectReferencePanel initialText="海康威视中标了哪些项目？" />);
    expect(screen.getByDisplayValue('海康威视中标了哪些项目？')).toBeInTheDocument();
  });

  it('输入不足 10 字仍禁用；够 10 字才可提交', () => {
    render(<ProjectReferencePanel />);
    fireEvent.change(screen.getByLabelText('项目概况文本'), { target: { value: '太短了' } });
    expect(screen.getByRole('button', { name: /查金额参考/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('项目概况文本'), { target: { value: VALID_TEXT } });
    expect(screen.getByRole('button', { name: /查金额参考/ })).toBeEnabled();
  });

  it('提交 → 金额区间（万元）+ 相似项目卡 + 查看原文 + 产品明细折叠', async () => {
    render(<ProjectReferencePanel />);
    await submit(VALID_TEXT);

    // 金额区间：fixture min 42.5 / max 597.92 → 固定 2 位小数万元
    expect(await screen.findByText('42.50 万元 ~ 597.92 万元')).toBeInTheDocument();
    expect(screen.getByText('金额参考')).toBeInTheDocument();
    expect(screen.getByText('4 个相似项目')).toBeInTheDocument();

    // 项目卡：项目名 + 中标金额 + 地区/日期 + 相似度
    expect(screen.getByText('校园安防视频监控系统升级改造项目')).toBeInTheDocument();
    expect(screen.getByText('133.94 万元')).toBeInTheDocument();
    expect(screen.getByText(/广东省/)).toBeInTheDocument();
    expect(screen.getByText('相似度 87%')).toBeInTheDocument();

    // 查看原文外链（ccgp 公告页，新标签打开）——mock 4 张卡各有 1 条，取第一条
    const links = screen.getAllByRole('link', { name: /查看原文/ }) as HTMLAnchorElement[];
    expect(links.length).toBeGreaterThan(0);
    expect(links[0].href).toContain('ccgp.gov.cn/cggg/');
    expect(links[0].target).toBe('_blank');

    // 产品明细折叠：展开后可见产品名 + 单价（元）
    fireEvent.click(screen.getByRole('button', { name: /产品明细（2）/ }));
    expect(screen.getByText(/400万星光级网络摄像机/)).toBeInTheDocument();
    expect(screen.getByText(/单价 1,650 元/)).toBeInTheDocument();
  });

  it('返回空结果 → 空态提示（不是白屏）', async () => {
    vi.spyOn(api.reference, 'fetch').mockResolvedValue({
      query: VALID_TEXT,
      total: 0,
      stats: null,
      projects: [],
    });

    render(<ProjectReferencePanel />);
    await submit(VALID_TEXT);

    expect(await screen.findByText('没有找到相似项目')).toBeInTheDocument();
    expect(screen.queryByText('金额参考')).toBeNull();
  });

  it('上传文件/图片 → 解析完成后产生 chip（复用主对话上传链路）', async () => {
    render(<ProjectReferencePanel />);

    const picker = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(picker).toBeTruthy();
    const file = new File(['x'], '招标文件.pdf', { type: 'application/pdf' });
    fireEvent.change(picker, { target: { files: [file] } });

    // mock 上传同步 done → chip 出现（带"就绪"状态）
    expect(await screen.findByText('招标文件.pdf')).toBeInTheDocument();
    expect(await screen.findByText(/1 个文件就绪/)).toBeInTheDocument();
  });

  it('供应商/中标类提问 → 引导移步供应商模块，不提交语义检索', async () => {
    const fetchSpy = vi.spyOn(api.reference, 'fetch');
    render(<ProjectReferencePanel />);

    fireEvent.change(screen.getByLabelText('项目概况文本'), {
      target: { value: '海康威视和中国移动集团的中标项目' },
    });
    fireEvent.click(screen.getByRole('button', { name: /查金额参考/ }));

    expect(await screen.findByText(/供应商全景/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('项目概况（含产品）不误触供应商引导，正常提交', async () => {
    render(<ProjectReferencePanel />);
    await submit(VALID_TEXT);
    expect(await screen.findByText('42.50 万元 ~ 597.92 万元')).toBeInTheDocument();
  });
});
