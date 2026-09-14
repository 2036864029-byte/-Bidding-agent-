import type { KanbanRawOverview } from '@/types/kanban';

/**
 * 商机看板 —— 高保真 Mock（**raw 形态**，模拟未来只读聚合端点）。
 *
 * 纪律：本文件存的是「后端会返回的原始形状」（snake_case），不是 UI 消费的
 * Domain 形态 —— 换真数据时只改 adapters/kanbanAdapter.ts + services/api.ts 开关。
 * 数值口径见 types/kanban.ts 文件头（月粒度 / 省级 / 金额元）。
 */
export const mockKanbanRaw: KanbanRawOverview = {
  summary: {
    total_projects: 1243,
    total_amount: 8_642_000_000, // 86.42 亿元
    active_projects: 512,
    closed_projects: 731,
    avg_competition: 6.8,
  },
  filters: {
    regions: [
      '广东省',
      '江苏省',
      '浙江省',
      '山东省',
      '河南省',
      '四川省',
      '湖北省',
      '福建省',
      '湖南省',
      '安徽省',
      '北京市',
      '上海市',
    ],
    industries: [
      '信息技术服务',
      '医疗卫生',
      '教育科研',
      '市政工程',
      '交通运输',
      '新能源',
      '公共安全',
      '环境治理',
    ],
    categories: ['信息化设备', '工程施工', '咨询服务', '医疗设备', '物业服务', '系统集成'],
  },
  heatmap: [
    { region: '广东省', count: 218, amount: 1_642_000_000 },
    { region: '江苏省', count: 176, amount: 1_286_000_000 },
    { region: '浙江省', count: 158, amount: 1_105_000_000 },
    { region: '山东省', count: 132, amount: 892_000_000 },
    { region: '河南省', count: 108, amount: 671_000_000 },
    { region: '四川省', count: 96, amount: 648_000_000 },
    { region: '湖北省', count: 84, amount: 556_000_000 },
    { region: '福建省', count: 76, amount: 512_000_000 },
    { region: '湖南省', count: 71, amount: 468_000_000 },
    { region: '安徽省', count: 63, amount: 421_000_000 },
    { region: '北京市', count: 37, amount: 263_000_000 },
    { region: '上海市', count: 24, amount: 178_000_000 },
  ],
  composition: [
    { industry: '信息技术服务', count: 321, amount: 2_418_000_000, pct: 25.8 },
    { industry: '医疗卫生', count: 208, amount: 1_536_000_000, pct: 16.7 },
    { industry: '市政工程', count: 187, amount: 1_742_000_000, pct: 15.0 },
    { industry: '教育科研', count: 164, amount: 902_000_000, pct: 13.2 },
    { industry: '交通运输', count: 128, amount: 1_068_000_000, pct: 10.3 },
    { industry: '新能源', count: 98, amount: 604_000_000, pct: 7.9 },
    { industry: '公共安全', count: 79, amount: 246_000_000, pct: 6.4 },
    { industry: '环境治理', count: 58, amount: 126_000_000, pct: 4.7 },
  ],
  trend: [
    { month: '2025-10', count: 88, amount: 592_000_000 },
    { month: '2025-11', count: 96, amount: 668_000_000 },
    { month: '2025-12', count: 134, amount: 1_024_000_000 },
    { month: '2026-01', count: 82, amount: 486_000_000 },
    { month: '2026-02', count: 64, amount: 358_000_000 },
    { month: '2026-03', count: 118, amount: 796_000_000 },
    { month: '2026-04', count: 126, amount: 884_000_000 },
    { month: '2026-05', count: 132, amount: 918_000_000 },
    { month: '2026-06', count: 141, amount: 1_042_000_000 },
    { month: '2026-07', count: 128, amount: 946_000_000 },
    { month: '2026-08', count: 134, amount: 928_000_000 },
  ],
  updated_at: '2026-09-14T09:00:00+08:00',
};
