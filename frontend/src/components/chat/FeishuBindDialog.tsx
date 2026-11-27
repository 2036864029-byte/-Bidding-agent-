import { AnimatePresence, motion } from 'framer-motion';
import { MessageCircle, Smartphone, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useChatWindowStore } from '@/store/chat-window';

/**
 * FeishuBindDialog —— 上传大文件后的「连接飞书」提醒（并行线 A）。
 *
 * 情况1（未绑定）：「您可以连接飞书，方便您在手机端了解任务执行进度」
 *   + 「连接飞书」按钮 → 拿 OAuth 授权链 → 顶层跳转；
 * 情况2（已绑定）：「您已经连接了飞书，可以随时在移动端了解任务执行进度」。
 *
 * 状态由 store 的 `feishuPrompt` 驱动（上传触发 checkFeishuPrompt，同会话只弹一次）。
 */
export function FeishuBindDialog() {
  const prompt = useChatWindowStore((s) => s.feishuPrompt);
  const connectFeishu = useChatWindowStore((s) => s.connectFeishu);
  const dismiss = useChatWindowStore((s) => s.dismissFeishuPrompt);

  const bound = prompt === 'bound';

  return (
    <AnimatePresence>
      {prompt && (
        <motion.div
          key="feishu-bind"
          className="fixed inset-0 z-[85] flex items-center justify-center px-4 py-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
        >
          <div
            className="absolute inset-0 bg-[oklch(24%_0.02_258/0.22)] backdrop-blur-sm"
            onClick={dismiss}
            aria-hidden
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={bound ? '已连接飞书' : '连接飞书'}
            tabIndex={-1}
            initial={{ opacity: 0, y: 16, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.99 }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            className="relative z-10 w-full max-w-[26rem] rounded-[var(--radius-xl)] border border-[var(--color-rule)] bg-[var(--color-paper)] shadow-[var(--shadow-float)] outline-none"
          >
            <header className="flex items-start justify-between gap-3 px-6 pt-5">
              <div className="flex items-center gap-2.5">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
                  {bound ? (
                    <MessageCircle className="h-4.5 w-4.5" aria-hidden />
                  ) : (
                    <Smartphone className="h-4.5 w-4.5" aria-hidden />
                  )}
                </span>
                <h2 className="text-[15.5px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                  {bound ? '已连接飞书' : '连接飞书看进度'}
                </h2>
              </div>
              <button
                type="button"
                onClick={dismiss}
                aria-label="关闭"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </header>

            <div className="px-6 pt-3 pb-6">
              {bound ? (
                <p className="text-[13.5px] leading-relaxed text-[var(--color-ink-2)]">
                  您已经连接了飞书，可以随时在移动端了解任务执行进度。
                </p>
              ) : (
                <>
                  <p className="text-[13.5px] leading-relaxed text-[var(--color-ink-2)]">
                    您可以连接飞书，方便您在手机端了解任务执行进度。
                  </p>
                  <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-neutral)]">
                    大文件解析需要一些时间。连接后，解析完成与结果摘要会推送到您的手机飞书，
                    离开电脑也能看到进度。
                  </p>
                </>
              )}

              <div className="mt-5 flex items-center justify-end gap-2.5">
                {bound ? (
                  <Button type="button" onClick={dismiss}>
                    知道了
                  </Button>
                ) : (
                  <>
                    <Button type="button" variant="secondary" onClick={dismiss}>
                      暂不
                    </Button>
                    <Button type="button" onClick={connectFeishu}>
                      <Smartphone className="h-3.5 w-3.5" aria-hidden />
                      连接飞书
                    </Button>
                  </>
                )}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
