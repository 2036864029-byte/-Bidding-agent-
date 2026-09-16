import type { KanbanRawOverview } from '@/types/kanban';

/**
 * 商机看板 —— 高保真 Mock（**raw 形态**，模拟 `/kanban/overview` 的真实返回）。
 *
 * 纪律：本文件存的是「后端会返回的原始形状」（snake_case），不是 UI 消费的
 * Domain 形态 —— 换真数据时只改 adapters/kanbanAdapter.ts + services/api.ts 开关。
 *
 * ⚠️ 样例结构必须与真实端点一致，否则 mock 期间看不出问题：
 *   - 热力图来自 `trade_stat_fact`（省级交易统计），带 `heatmap_month`
 *   - 行业走势也是**交易类型** × 月，且**最多 3 条**（all-vs-all 的 CVD 硬约束）
 *   - 没有 `trend`（商机走势已撤，被 2023-10 异常月压平）、没有 `product_rank`（分布无信号）
 */
export const mockKanbanRaw: KanbanRawOverview = {
  summary: {
    total_projects: 18497,
    recent_projects: 755,
    regions: 32,
    industries: 30,
  },
  filters: {
    regions: [
      '上海市',
      '四川省',
      '浙江省',
      '广东省',
      '山东省',
      '安徽省',
      '江苏省',
      '河北省',
      '辽宁省',
      '江西省',
      '黑龙江省',
      '福建省',
    ],
    industries: [
      '学校',
      '政府',
      '货物',
      '服务',
      '政府采购',
      '工程',
      '房屋建筑',
      '医院',
      '市政',
      '项目管理',
      '公路',
      '水利',
    ],
    categories: ['学校', '政府', '货物', '服务', '政府采购', '工程'],
  },
  heatmap: [
    { region: '上海市', count: 4698 },
    { region: '四川省', count: 4104 },
    { region: '浙江省', count: 2748 },
    { region: '广东省', count: 2353 },
    { region: '山东省', count: 2046 },
    { region: '安徽省', count: 1845 },
    { region: '江苏省', count: 1660 },
    { region: '河北省', count: 1640 },
    { region: '辽宁省', count: 1380 },
    { region: '江西省', count: 1275 },
    { region: '黑龙江省', count: 1268 },
    { region: '福建省', count: 1230 },
  ],
  heatmap_month: '2026-09-01',
  composition: [
    { industry: '学校', count: 3246, pct: 20.1 },
    { industry: '政府', count: 2602, pct: 16.1 },
    { industry: '货物', count: 2251, pct: 13.9 },
    { industry: '服务', count: 2178, pct: 13.5 },
    { industry: '政府采购', count: 2125, pct: 13.1 },
    { industry: '工程', count: 909, pct: 5.6 },
    { industry: '房屋建筑', count: 836, pct: 5.2 },
    { industry: '医院', count: 575, pct: 3.6 },
  ],
  // 交易类型 × 月（最多 3 条）；数值取自真实端点的实测输出
  industry_trend: {
    months: [
      '2025-11',
      '2026-01',
      '2026-02',
      '2026-03',
      '2026-04',
      '2026-05',
      '2026-06',
      '2026-07',
      '2026-08',
      '2026-09',
    ],
    series: [
      {
        name: '政府采购',
        values: [53044, 38857, 23813, 35476, 44655, 46796, 53788, 56325, 50962, 18920],
      },
      {
        name: '其他',
        values: [31139, 25788, 20756, 58533, 148251, 47594, 34495, 29297, 12207, 9681],
      },
      {
        name: '工程建设',
        values: [22097, 19467, 13088, 18916, 17527, 19467, 18750, 20307, 17560, 7109],
      },
    ],
  },
  updated_at: '2026-09-15T14:01:13',
};
