import { num, str, strArray } from '@/adapters/helpers';
import type {
  Supplier,
  SupplierOverview,
  SupplierRawItem,
  SupplierRawOverview,
  SupplierWinRecord,
} from '@/types/supplier';

/**
 * supplierAdapter —— 供应商 raw → Domain。
 *
 * 宽容点：字段普遍可缺失（CCGP 侧没有工商信息），所以**一律不填假值**——
 * 缺就是 `undefined`，让 UI 决定怎么表达"没有"，而不是伪造 `0` 或 `''`。
 * 只有名称兜底（无名无法展示），中标次数/金额兜底 0（真值可能就是 0）。
 */

const optStr = (v: unknown): string | undefined => {
  const s = str(v).trim();
  return s === '' ? undefined : s;
};

function adaptWin(w: NonNullable<SupplierRawItem['recent_wins']>[number]): SupplierWinRecord {
  const amt = w.winning_amount;
  return {
    projectNo: str(w.project_no),
    projectName: str(w.project_name, '（未命名项目）'),
    region: str(w.region, '—'),
    category: optStr(w.category),
    // 金额缺失保留 null（区别于 0）——0 在中标场景里表示"未公开"而非"零元"
    winningAmount:
      amt === null || amt === undefined || str(amt) === '' ? null : num(amt),
    winningDate: str(w.winning_date, '—'),
  };
}

function adaptSupplier(s: SupplierRawItem): Supplier {
  return {
    id: str(s.id),
    source: str(s.source, 'UNKNOWN'),
    name: str(s.name, '未命名企业'),

    creditCode: optStr(s.credit_code),
    province: optStr(s.province),
    city: optStr(s.city),
    district: optStr(s.district),
    industry: optStr(s.industry),
    legalPerson: optStr(s.legal_person),
    registeredCapital: optStr(s.registered_capital),
    establishDate: optStr(s.establish_date),
    businessStatus: optStr(s.business_status),
    companyType: optStr(s.company_type),
    companyLevel: optStr(s.company_level),
    address: optStr(s.address),
    businessScope: optStr(s.business_scope),

    winCount: num(s.win_count),
    winAmountTotal: num(s.win_amount_total),
    firstWinDate: optStr(s.first_win_date),
    lastWinDate: optStr(s.last_win_date),
    mainRegion: optStr(s.main_region),
    recentWins: (s.recent_wins ?? []).map(adaptWin),
  };
}

/** 没用到 trade_types —— 保留在 raw 里但 Domain 暂不消费（不建无用字段）。 */
export function supplierAdapter(raw?: SupplierRawOverview | null): SupplierOverview {
  const data = raw ?? {};
  const filters = data.filters ?? {};
  return {
    filters: {
      provinces: strArray(filters.provinces),
      industries: strArray(filters.industries),
    },
    total: num(data.total),
    returned: num(data.returned),
    suppliers: (data.suppliers ?? []).map(adaptSupplier),
  };
}
