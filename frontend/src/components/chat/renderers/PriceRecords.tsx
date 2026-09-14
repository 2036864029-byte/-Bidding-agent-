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
 * 「在询价模块查看详情」是**前端自建**的联动入口（后端不产这个信号，见设计文档 §1 修正 1）：
 * 结果本身就是一批可比价的项目，跳去询价模块看对比表是自然的下一步。
 */
export function PriceRecords({ message }: { message: ChatMessage }) {
  const data = message.data;
  const records = data?.kind === 'price_records' ? data.records : [];
  const warn = errorText(message.errorCode);

  return (
    <div>
      <Markdown>{message.content}</Markdown>
      {warn && <Notice>{warn}</Notice>}

      {records.length > 0 && (
        <div className="mt-3.5">
          <PriceRecordsTable records={records} />
          <div className="mt-2.5 flex justify-end">
            <Link
              to="/inquiry"
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-accent-soft)]"
            >
              在询价模块查看详情
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
