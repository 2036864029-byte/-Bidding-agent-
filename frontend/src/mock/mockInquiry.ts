import type { InquiryRawSummary } from '@/types/inquiry';

/**
 * 智能询价 —— 高保真 Mock（**raw 形态**）。
 *
 * 字段键刻意采用「双写」形状（`id`/`inquiryId`、`price`/`unitPrice`…），
 * 与规划方案 §4.2 的适配器示例一致 —— 用于验证 adapters/ 的宽容映射：
 * 未来真后端无论给哪一套键名，UI 都不受影响。
 */
export const mockInquiryRaw: InquiryRawSummary = {
  id: 'INQ-2026-0910-0031',
  title: '智慧园区安防与信息化设备采购',
  status: 'IN_PROGRESS',
  budget: 2_860_000,
  created_at: '2026-09-10T09:30:00+08:00',
  items: [
    {
      item_id: 'ITM-01',
      name: '高清网络摄像机',
      spec: '400 万像素 星光级 红外 50m',
      unit: '台',
      qty: 120,
      base_price: 850,
      budget_threshold: 1000,
      supplier_quotes: [
        {
          supplier_id: 'SUP-001',
          supplier_name: '杭州海康威视数字技术股份有限公司',
          price: 820,
          deviation: -3.5,
          is_best: true,
          reason: '本地化服务响应最快，历史履约零投诉',
        },
        {
          supplier_id: 'SUP-002',
          supplier_name: '浙江大华技术股份有限公司',
          price: 875,
          deviation: 2.9,
        },
        {
          supplier_id: 'SUP-003',
          supplier_name: '深圳市捷顺科技实业股份有限公司',
          price: 1040,
          deviation: 22.4,
          reason: '报价含三年质保，但单价超阈值',
        },
      ],
    },
    {
      item_id: 'ITM-02',
      name: '门禁一体机',
      spec: '人脸识别 8 寸屏 支持口罩识别',
      unit: '台',
      qty: 60,
      base_price: 1250,
      budget_threshold: 1450,
      supplier_quotes: [
        {
          supplier_id: 'SUP-002',
          supplier_name: '浙江大华技术股份有限公司',
          price: 1180,
          deviation: -5.6,
          is_best: true,
        },
        {
          supplier_id: 'SUP-001',
          supplier_name: '杭州海康威视数字技术股份有限公司',
          price: 1290,
          deviation: 3.2,
        },
      ],
    },
    {
      item_id: 'ITM-03',
      name: '综合安防管理平台软件',
      spec: '支持 5000 路接入 / 级联 / 开放 API',
      unit: '套',
      qty: 1,
      base_price: 180000,
      budget_threshold: 200000,
      supplier_quotes: [
        {
          supplier_id: 'SUP-001',
          supplier_name: '杭州海康威视数字技术股份有限公司',
          price: 176000,
          deviation: -2.2,
          is_best: true,
        },
        {
          supplier_id: 'SUP-004',
          supplier_name: '新华三技术有限公司',
          price: 195000,
          deviation: 8.3,
          reason: '含 5 年原厂维保',
        },
      ],
    },
  ],
};
