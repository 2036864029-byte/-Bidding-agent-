/**
 * 友情链接数据 —— 招投标领域的政府级网站。
 *
 * ⚠️ **每条都在 2026-09-16 逐个发请求核验过**（HTTP 200 + 页面标题与站名一致），
 *    不是凭印象拼的域名。政府网站改版/迁移很常见，**改这里之前请重新核验**：
 *    核验脚本 `frontend/.shots/check_links.py`（逐个打状态码与 <title>）。
 *
 * 只收录 .gov.cn 与国家级行业平台。**不收录**：`.com` 域名的跳转占位页、
 * 无法确认主办单位的站点（本次已据此弃用「深圳公共资源交易中心 szggzy.com」）。
 */

export interface GovLink {
  name: string;
  url: string;
  /** 展示用域名（不含协议与末尾斜杠） */
  domain: string;
  /** 一句话说明「这里能查到什么」 */
  note: string;
  /** 省市站标注属地 */
  region?: string;
}

export interface LinkGroup {
  id: string;
  name: string;
  blurb: string;
  items: GovLink[];
}

/** 核验日期（页面上会显示，便于判断这页信息有多新）。 */
export const VERIFIED_ON = '2026-09-16';

export const GOVERNMENT_LINKS: LinkGroup[] = [
  {
    id: 'national',
    name: '国家级',
    blurb: '全国范围的采购与交易信息发布渠道，以及招标投标的主管部门',
    items: [
      {
        name: '中国政府采购网',
        url: 'https://www.ccgp.gov.cn/',
        domain: 'www.ccgp.gov.cn',
        note: '财政部主管，政府采购公告、政策法规与采购意向发布的主渠道。本系统收录的中标公告原文链接多指向这里。',
      },
      {
        name: '全国公共资源交易平台',
        url: 'https://www.ggzy.gov.cn/',
        domain: 'www.ggzy.gov.cn',
        note: '全国公共资源交易信息的国家级汇总入口，覆盖工程建设、政府采购、土地与产权交易。',
      },
      {
        name: '中国招标投标公共服务平台',
        url: 'https://www.cebpubservice.com/',
        domain: 'www.cebpubservice.com',
        note: '招标投标行业公共服务平台，汇集项目公告、市场主体与信用信息。',
      },
      {
        name: '国家发展和改革委员会',
        url: 'https://www.ndrc.gov.cn/',
        domain: 'www.ndrc.gov.cn',
        note: '招标投标工作的主管部门，须招标项目范围、招标投标法规与政策在此发布。',
      },
    ],
  },
  {
    id: 'local',
    name: '省市',
    blurb: '地方采购与交易平台。各地公告格式与栏目差异较大，跨地区查项目时建议同时对照国家级平台',
    items: [
      {
        name: '北京市公共资源交易服务平台',
        url: 'https://ggzyfw.beijing.gov.cn/',
        domain: 'ggzyfw.beijing.gov.cn',
        region: '北京',
        note: '北京市公共资源交易信息发布与查询入口。',
      },
      {
        name: '上海市政府采购网',
        url: 'https://www.zfcg.sh.gov.cn/',
        domain: 'www.zfcg.sh.gov.cn',
        region: '上海',
        note: '上海市政府采购公告、中标结果与政策文件。',
      },
      {
        name: '广东省政府采购网',
        url: 'https://gdgpo.czt.gd.gov.cn/',
        domain: 'gdgpo.czt.gd.gov.cn',
        region: '广东',
        note: '广东省政府采购信息发布平台（页面由脚本渲染，首次打开可能稍慢）。',
      },
      {
        name: '浙江政府采购网',
        url: 'https://zfcg.czt.zj.gov.cn/',
        domain: 'zfcg.czt.zj.gov.cn',
        region: '浙江',
        note: '浙江省政府采购公告与采购结果公开。',
      },
      {
        name: '河南省政府采购网',
        url: 'https://zfcg.henan.gov.cn/',
        domain: 'zfcg.henan.gov.cn',
        region: '河南',
        note: '河南省政府采购信息发布与合同公开。',
      },
      {
        name: '四川政府采购网',
        url: 'https://www.ccgp-sichuan.gov.cn/',
        domain: 'www.ccgp-sichuan.gov.cn',
        region: '四川',
        note: '四川省政府采购公告与结果公告（页面由脚本渲染）。',
      },
    ],
  },
];
