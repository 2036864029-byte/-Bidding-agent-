import { DeepAgent } from '@/components/chat/renderers/DeepAgent';
import { DocQa } from '@/components/chat/renderers/DocQa';
import { KbAnswer } from '@/components/chat/renderers/KbAnswer';
import { PlainText, Safety } from '@/components/chat/renderers/PlainText';
import { PriceRecords } from '@/components/chat/renderers/PriceRecords';
import type { ChatMessage } from '@/types/chat';

/**
 * ReplyRenderer —— reply_type 渲染注册表。
 *
 * 后端 `data` 是**通用容器**（不同分支键不同），所以按判别式分发，
 * **不要假设固定字段**（接口清单 §3.2）。7 种 reply_type 全覆盖：
 *   kb_answer    正文 + 引用卡片
 *   price_records 正文 + 中标记录表 + 询价模块联动
 *   doc_qa       正文 + 来源列表 + 显式兜底提示
 *   deep_agent   正文 + 思维链折叠面板（唯一有真 trace 的分支）
 *   safety       安全边界提示
 *   chat / fallback  纯文本
 *
 * 流式过程中（还没拿到 result）`replyType` 还是空的 —— 先按纯文本渲染 token，
 * result 到达后本组件自动切到对应分支。
 */
export function ReplyRenderer({ message }: { message: ChatMessage }) {
  // data.kind 与 replyType 同源；优先用 data.kind 以获得类型收窄
  switch (message.data?.kind ?? message.replyType) {
    case 'kb_answer':
      return <KbAnswer message={message} />;
    case 'price_records':
      return <PriceRecords message={message} />;
    case 'doc_qa':
      return <DocQa message={message} />;
    case 'deep_agent':
      return <DeepAgent message={message} />;
    case 'safety':
      return <Safety message={message} />;
    case 'chat':
    case 'fallback':
    default:
      return <PlainText message={message} />;
  }
}
