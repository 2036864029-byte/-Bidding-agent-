/**
 * mockReference —— 项目概况参考（POST /inquiry/reference）的离线演示数据。
 *
 * 与真实返回**同形状**（snake_case raw），经 `referenceAdapter` 归一 —— mock 态
 * 走与 api 态完全相同的适配路径，测试才能真正代表线上行为。
 *
 * 数据取自真实中标公告的合理编排（金额万元、公告原文链接为 ccgp 具体公告页、
 * 产品单价元），仅用于无后端时的演示与页面测试。
 */
import type { ReferenceRawResponse } from '@/types/inquiry';

export const mockReferenceRaw: ReferenceRawResponse = {
  query: '采购120台监控摄像头用于校园安防监控项目',
  total: 4,
  amount_stats: {
    min_wan: 42.5,
    median_wan: 133.94,
    max_wan: 597.92,
    avg_wan: 231.35,
    sample_count: 4,
  },
  projects: [
    {
      announcement_id: 't20260911_27310629',
      project_no: 'SG-2026-0911',
      project_name: '校园安防视频监控系统升级改造项目',
      purchaser: '某市第一中学',
      region: '广东省',
      winning_amount_wan: 133.94,
      winning_date: '2026-09-11',
      source_url: 'https://www.ccgp.gov.cn/cggg/zygg/zbgg/202609/t20260911_27310629.htm',
      score: 0.87,
      products: [
        {
          product_name: '400万星光级网络摄像机',
          normalized_product_name: '网络摄像机',
          brand: '海康威视',
          model: 'DS-2CD3T46WDV3',
          quantity: '120',
          unit: '台',
          unit_price_yuan: 1650,
        },
        {
          product_name: '视频存储服务器',
          normalized_product_name: '存储服务器',
          brand: '海康威视',
          model: 'DS-AT1000S',
          quantity: '2',
          unit: '台',
          unit_price_yuan: 45800,
        },
      ],
    },
    {
      announcement_id: 't20260908_27291455',
      project_no: 'GY-ZB-2026-118',
      project_name: '手术示教系统建设采购项目',
      purchaser: '某市人民医院',
      region: '上海市',
      winning_amount_wan: 402.0,
      winning_date: '2026-09-08',
      source_url: 'https://www.ccgp.gov.cn/cggg/zygg/zbgg/202609/t20260908_27291455.htm',
      score: 0.72,
      products: [
        {
          product_name: '4K手术示教系统',
          normalized_product_name: '手术示教系统',
          brand: '锐取',
          model: 'RZ-4K',
          quantity: '8',
          unit: '套',
          unit_price_yuan: 170000,
        },
      ],
    },
    {
      announcement_id: 't20260914_27323008',
      project_no: 'ZZCG-2026-220',
      project_name: '高校食堂食品安全视频监控系统采购项目',
      purchaser: '某大学后勤处',
      region: '湖北省',
      winning_amount_wan: 52.72,
      winning_date: '2026-09-14',
      source_url: 'https://www.ccgp.gov.cn/cggg/zygg/zbgg/202609/t20260914_27323008.htm',
      score: 0.58,
      products: [
        {
          product_name: '食堂监控摄像机',
          normalized_product_name: '监控摄像机',
          brand: '大华',
          model: 'DH-IPC-HFW4',
          quantity: '60',
          unit: '台',
          unit_price_yuan: 980,
        },
      ],
    },
    {
      announcement_id: 't20260910_27301181',
      project_no: 'XF-2026-77',
      project_name: '消防通道智能识别摄像头采购项目',
      purchaser: '某区消防救援支队',
      region: '浙江省',
      winning_amount_wan: 597.92,
      winning_date: '2026-09-10',
      source_url: 'https://www.ccgp.gov.cn/cggg/zygg/zbgg/202609/t20260910_27301181.htm',
      score: 0.51,
      products: [
        {
          product_name: '智能识别摄像头',
          normalized_product_name: '摄像头',
          brand: '宇视',
          model: 'IPC6324',
          quantity: '200',
          unit: '台',
          unit_price_yuan: 2150,
        },
      ],
    },
  ],
};
