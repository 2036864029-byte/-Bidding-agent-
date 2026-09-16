import { Link } from 'react-router';
import { ArrowUpRight } from 'lucide-react';
import { PriceRecordsTable } from '@/components/chat/PriceRecordsTable';
import { Markdown } from '@/components/chat/Markdown';
import { Notice } from '@/components/chat/renderers/KbAnswer';
import { errorText } from '@/lib/chat';
import type { ChatMessage } from '@/types/chat';

/**
 * PriceRecords —— 价格/标讯查询（`price_records`）：正文 + 中标记录表。
 *
 * 联动入口是**前端自建**的（后端不产这个信号，见设计文档 §1 修正 1）：
 *   供应商类结果（company_query 子路由：推荐供应商/公司详情/不良记录）
 *     → 跳**供应商模块**（按公司名搜索，直接看工商+中标）；
 *   标讯类结果（bidding_query / 采购需求等）
 *     → 跳**询价模块**（看相似项目对比表）。
 * 跳转都带上原始提问，让目标页接得上上下文。
 */
export function PriceRecords({ message }: { message: ChatMessage }) {
  const data = message.data;
  const price = data?.kind === 'price_records' ? data : null;
  const warn = errorText(message.errorCode);

  // 供应商类问题 → 供应商模块；其余标讯 → 询价模块
  // 供应商模块按公司名搜索：优先取首条记录的公司名（比整句原始提问更精准），
  // 记录缺公司名时回退到原始提问。
  const isSupplier = price?.intent?.subRoute === 'company_query';
  const carried = isSupplier
    ? price?.records[0]?.company_name || message.sourceQuestion || ''
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
