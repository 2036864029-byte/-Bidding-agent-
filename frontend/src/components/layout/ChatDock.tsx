import { useChatWindowStore } from '@/store/chat-window';
import { ChatInputRow } from '@/components/chat/ChatInputRow';
import { ChatResumePill } from '@/components/chat/ChatResumePill';

/**
 * ChatDock —— 底部输入条（D5/D7）。
 * 渐变描边签名（唯一发光点）；容器与三卡片区同宽 72rem（版心对齐）。
 * 浮窗打开时隐藏输入（输入移入浮窗）；收起后输入条回归 + 「对话中」胶囊。
 */
export function ChatDock() {
  const { status, messages, input, setInput, submit, uploadFiles, busy } = useChatWindowStore();

  if (status === 'open') return null; // 聚焦浮窗持输入

  return (
    <section className="mx-auto w-full max-w-[72rem] px-6 pb-10 pt-6">
      <ChatInputRow
        value={input}
        onChange={setInput}
        onSend={submit}
        onUpload={uploadFiles}
        busy={busy}
        placeholder="输入你的问题，例如：招标方式有哪些？"
      />
      {status === 'minimized' && messages.length > 0 && (
        <div className="mt-4 flex justify-center">
          <ChatResumePill />
        </div>
      )}
    </section>
  );
}
