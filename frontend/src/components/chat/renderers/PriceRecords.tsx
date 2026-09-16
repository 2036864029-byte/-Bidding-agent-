import { Link } from 'react-router';
import { ArrowUpRight } from 'lucide-react';
import { PriceRecordsTable } from '@/components/chat/PriceRecordsTable';
import { Markdown } from '@/components/chat/Markdown';
import { Notice } from '@/components/chat/renderers/KbAnswer';
import { errorText } from '@/lib/chat';
import type { ChatMessage, PriceIntent, PriceRecordRow } from '@/types/chat';

/**
 * PriceRecords —— 价格/标讯查询（`price_records`）：正文 + 中标记录表。
 *
 * 联动入口是**前端自建**的（后端不产这个信号，见设计文档 §1 修正 1）：
 *   公司类结果 → 跳**供应商模块**（按公司名搜索，直接看工商+中标）；
 *     ① `company_query` 子路由（推荐供应商/公司详情/不良记录）
 *     ② `bidding_query` + `bidder_query`（"某公司中标了哪些项目"——主语是公司，
 *        落脚点是它的中标业绩，同样属于供应商全景）
 *   标讯类结果（项目/采购人/采购需求）→ 跳**询价模块**（看相似项目对比表）。
 * 跳转都带上上下文，让目标页接得上。
 */

/** 记录里的公司名：企业查询是 `company_name`，中标查询是 `successful_bidder`。 */
function rowCompany(r: PriceRecordRow | undefined): string {
  const c = r?.company_name;
  if (typeof c === 'string' && c) return c;
  const s = r?.successful_bidder;
  return typeof s === 'string' ? s : '';
}

/** 是否"以公司为主语"的查询 —— 决定跳供应商模块还是询价模块。 */
export function isCompanyCentric(intent: PriceIntent | null | undefined): boolean {
  return intent?.subRoute === 'company_query' || intent?.queryType === 'bidder_query';
}

export function PriceRecords({ message }: { message: ChatMessage }) {
  const data = message.data;
  const price = data?.kind === 'price_records' ? data : null;
  const warn = errorText(message.errorCode);

  // 供应商模块按公司名搜索（`normalized_name LIKE %kw%`，整句提问搜不到）。
  // 搜索词优先级：语义关键词（**家族查询**如「中国移动」能一次命中全部子公司）
  //   → 首条记录的公司名 → 原始提问。
  // 关键词可能是行业词（如「软件」）——只有真在结果公司名里出现才算数，否则丢弃。
  const kwRaw = price?.intent?.semanticKeywords?.[0]?.trim();
  const kw =
    kwRaw && price?.records.some((r) => rowCompany(r).includes(kwRaw)) ? kwRaw : '';

  const isSupplier = isCompanyCentric(price?.intent);
  const carried = isSupplier
    ? kw || rowCompany(price?.records[0]) || message.sourceQuestion || ''
    : message.sourceQuestion || '';
  const base = isSupplier ? '/supplier?from=chat' : '/inquiry?from=chat';
  const target = carried ? `${base}&q=${encodeURIComponent(carried)}` : base;

  return (
    <div>
      <Markdown>{message.content}</Markdown>
      {warn && <Notice>{warn}</Notice>}

      {price && price.records.length > 0 && (
        <div className="mt-3.5">
          <PriceRecordsTable records={price.records} columns={price.columns} />
          <div className="mt-2.5 flex justify-end">
            <Link
              to={target}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-accent-soft)]"
            >
              {isSupplier ? '在供应商模块查看详情' : '在询价模块查看详情'}
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
