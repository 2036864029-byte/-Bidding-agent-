import type { KanbanRawOverview } from '@/types/kanban';

/**
 * 商机看板 —— 高保真 Mock（**raw 形态**，模拟未来只读聚合端点）。
 *
 * 纪律：本文件存的是「后端会返回的原始形状」（snake_case），不是 UI 消费的
 * Domain 形态 —— 换真数据时只改 adapters/kanbanAdapter.ts + services/api.ts 开关。
 *
 * 口径（2026-09-14 用户拍板）：**金额一律「万元」**；月粒度（按中标日期）；
 * 省级地区；项目类别；不做「进行中/已截止」状态。
 */
export const mockKanbanRaw: KanbanRawOverview = {
  summary: {
    total_projects: 1243,
    total_amount: 864_200, // 万元（≈ 86.42 亿元）
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
    { region: '广东省', count: 218, amount: 164_200 },
    { region: '江苏省', count: 176, amount: 128_600 },
    { region: '浙江省', count: 158, amount: 110_500 },
    { region: '山东省', count: 132, amount: 89_200 },
    { region: '河南省', count: 108, amount: 67_100 },
    { region: '四川省', count: 96, amount: 64_800 },
    { region: '湖北省', count: 84, amount: 55_600 },
    { region: '福建省', count: 76, amount: 51_200 },
    { region: '湖南省', count: 71, amount: 46_800 },
    { region: '安徽省', count: 63, amount: 42_100 },
    { region: '北京市', count: 37, amount: 26_300 },
    { region: '上海市', count: 24, amount: 17_800 },
  ],
  composition: [
    { industry: '信息技术服务', count: 321, amount: 241_800, pct: 25.8 },
    { industry: '医疗卫生', count: 208, amount: 153_600, pct: 16.7 },
    { industry: '市政工程', count: 187, amount: 174_200, pct: 15.0 },
    { industry: '教育科研', count: 164, amount: 90_200, pct: 13.2 },
    { industry: '交通运输', count: 128, amount: 106_800, pct: 10.3 },
    { industry: '新能源', count: 98, amount: 60_400, pct: 7.9 },
    { industry: '公共安全', count: 79, amount: 24_600, pct: 6.4 },
    { industry: '环境治理', count: 58, amount: 12_600, pct: 4.7 },
  ],
  trend: [
    { month: '2025-10', count: 88, amount: 59_200 },
    { month: '2025-11', count: 96, amount: 66_800 },
    { month: '2025-12', count: 134, amount: 102_400 },
    { month: '2026-01', count: 82, amount: 48_600 },
    { month: '2026-02', count: 64, amount: 35_800 },
    { month: '2026-03', count: 118, amount: 79_600 },
    { month: '2026-04', count: 126, amount: 88_400 },
    { month: '2026-05', count: 132, amount: 91_800 },
    { month: '2026-06', count: 141, amount: 104_200 },
    { month: '2026-07', count: 128, amount: 94_600 },
    { month: '2026-08', count: 134, amount: 92_800 },
  ],
  updated_at: '2026-09-14T09:00:00+08:00',
};
