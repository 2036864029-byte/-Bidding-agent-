import type { SupplierRawOverview } from '@/types/supplier';

/**
 * 供应商 —— 高保真 Mock（**raw 形态**，模拟 `/suppliers/overview` 的真实返回）。
 *
 * 纪律：本文件存「后端会返回的原始形状」（snake_case），换真数据只改
 * adapters/supplierAdapter.ts + services/api.ts 开关。
 *
 * 样例刻意**覆盖两种来源**，与真实端点一致：
 *   - LEGACY：有完整工商信息（省份/行业/法人/注册资本…）
 *   - CCGP：新数据那 1,058 家的形态 —— **没有工商字段**，只有名称 + 中标业绩。
 *     这能防止 UI 只在"字段齐全"的样例上看起来正常。
 */
export const mockSupplierRaw: SupplierRawOverview = {
  filters: {
    provinces: ['安徽省', '广东省', '重庆市', '山东省', '北京市', '浙江省', '江苏省'],
    industries: [
      '软件和信息技术服务业',
      '批发业',
      '土木工程建筑业',
      '公共设施管理业',
      '零售业',
      '研究和试验发展',
    ],
  },
  total: 39969,
  returned: 3,
  suppliers: [
    {
      id: 'LEGACY:1024',
      source: 'LEGACY',
      name: '安徽海纳信息科技有限公司',
      credit_code: '91341300MA2MTHUL8L',
      province: '安徽省',
      city: '宿州市',
      industry: '软件和信息技术服务业',
      legal_person: '郭振',
      registered_capital: '500万人民币',
      establish_date: '2016-03-03',
      business_status: '存续',
      company_type: '有限责任公司(自然人投资或控股)',
      company_level: '小型企业',
      address: '宿州市高新技术产业开发区拱辰路8号',
      business_scope:
        '计算机软硬件及技术服务；信息系统集成；安防监控系统、网络工程、电子LED显示屏、数字化会议及广播视频系统研发及销售；电子产品、仪器仪表、办公设备、家具销售。',
      win_count: 12,
      win_amount_total: 48620000,
      first_win_date: '2019-04-18',
      last_win_date: '2025-11-04',
      main_region: '安徽省',
      recent_wins: [
        {
          project_no: 'ZTB-2025-AH-004182',
          project_name: '宿州市政务服务中心安防监控系统升级项目',
          region: '安徽省',
          category: '信息化设备',
          winning_amount: 4860000,
          winning_date: '2025-11-04',
        },
        {
          project_no: 'ZTB-2023-AH-002771',
          project_name: '宿州经开区智慧园区管理平台建设项目',
          region: '安徽省',
          category: '系统集成',
          winning_amount: 8250000,
          winning_date: '2023-09-12',
        },
        {
          project_no: 'ZTB-2019-AH-000318',
          project_name: '宿州市某中学校园网络改造工程',
          region: '安徽省',
          category: '信息化设备',
          winning_amount: 1560000,
          winning_date: '2019-04-18',
        },
      ],
    },
    {
      id: 'LEGACY:2048',
      source: 'LEGACY',
      name: '广东省机电设备招标中心有限公司',
      credit_code: '91440000190323456X',
      province: '广东省',
      city: '广州市',
      district: '越秀区',
      industry: '商务服务业',
      legal_person: '陈志强',
      registered_capital: '1000万人民币',
      establish_date: '2009-07-21',
      business_status: '存续',
      company_type: '有限责任公司(国有控股)',
      company_level: '中型企业',
      address: '广州市越秀区东风东路726号',
      business_scope: '招标代理服务；政府采购代理；工程造价咨询；工程项目管理。',
      win_count: 3,
      win_amount_total: 7240000,
      first_win_date: '2021-03-05',
      last_win_date: '2024-06-30',
      main_region: '广东省',
      recent_wins: [
        {
          project_no: 'ZTB-2024-GD-003176',
          project_name: '省级政务云视频监控扩容项目',
          region: '广东省',
          category: '系统集成',
          winning_amount: 2880000,
          winning_date: '2024-06-30',
        },
      ],
    },
    {
      // CCGP 形态：没有工商字段 —— UI 必须能优雅处理
      id: 'CCGP:517',
      source: 'CCGP',
      name: '湖北楚天智能交通股份有限公司',
      credit_code: '9142000070654312XY',
      win_count: 1,
      win_amount_total: 41700000000,
      first_win_date: '2024-02-15',
      last_win_date: '2024-02-15',
      main_region: '湖北省',
      recent_wins: [
        {
          project_no: 'HBSJ-202307GL-056001001',
          project_name: '沪渝高速公路武汉至宜昌段改扩建工程投资人招标',
          region: '湖北省',
          category: null as unknown as string,
          winning_amount: 41700000000,
          winning_date: '2024-02-15',
        },
      ],
    },
  ],
};
