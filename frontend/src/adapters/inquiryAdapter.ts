import { num, pick, str } from '@/adapters/helpers';
import type {
  InquiryItem,
  InquiryRawItem,
  InquiryRawSummary,
  InquiryStatus,
  InquirySummary,
} from '@/types/inquiry';

/** 状态归一：未知值回落 IN_PROGRESS（询价单的常见默认态）。 */
function normalizeStatus(v: unknown): InquiryStatus {
  const s = str(v).toUpperCase();
  return s === 'DRAFT' || s === 'COMPLETED' || s === 'IN_PROGRESS' ? s : 'IN_PROGRESS';
}

/** 偏离阈值缺省：基准单价上浮 15%。 */
const DEFAULT_THRESHOLD_RATIO = 1.15;

function adaptItem(item: InquiryRawItem): InquiryItem {
  const quantity = num(pick(item.qty, item.targetQuantity));
  const benchmarkUnitPrice = num(pick(item.base_price, item.benchmarkUnitPrice));
  const thresholdRaw = num(pick(item.budget_threshold), Number.NaN);

  return {
    itemId: str(pick(item.item_id, item.itemId)),
    itemName: str(pick(item.name, item.itemName), '未命名项'),
    specifications: str(pick(item.spec, item.specifications), '-'),
    unit: str(item.unit, '件'),
    targetQuantity: quantity,
    benchmarkUnitPrice,
    budgetThreshold: Number.isFinite(thresholdRaw)
      ? thresholdRaw
      : Math.round(benchmarkUnitPrice * DEFAULT_THRESHOLD_RATIO),
    quotes: (pick(item.supplier_quotes, item.quotes) ?? []).map((q) => {
      const unitPrice = num(pick(q.price, q.unitPrice));
      const reason = str(q.reason);
      return {
        supplierId: str(pick(q.supplier_id, q.supplierId)),
        supplierName: str(pick(q.supplier_name, q.supplierName), '未知供应商'),
        unitPrice,
        totalPrice: unitPrice * quantity,
        isRecommended: Boolean(pick(q.is_best, q.isRecommended)),
        deviationRate: num(pick(q.deviation, q.deviationRate)),
        ...(reason ? { reason } : {}),
      };
    }),
  };
}

/**
 * inquiryAdapter —— 智能询价 raw → Domain（沿用规划方案 §4.2 的宽容映射范式）。
 *
 * 宽泛点：`id|inquiryId`、`name|itemName`、`price|unitPrice`、`is_best|isRecommended`
 * 等双字段一律兜底 —— 未来真后端给哪一套键名都无需改 UI。
 */
export function inquiryAdapter(raw?: InquiryRawSummary | null): InquirySummary {
  const data = raw ?? {};
  const createdAt = str(pick(data.created_at, data.createdAt));

  return {
    inquiryId: str(pick(data.id, data.inquiryId)),
    title: str(data.title, '智能询价单'),
    status: normalizeStatus(data.status),
    totalBudget: num(pick(data.budget, data.totalBudget)),
    createdAt: createdAt || new Date().toISOString(),
    items: (data.items ?? []).map(adaptItem),
  };
}
