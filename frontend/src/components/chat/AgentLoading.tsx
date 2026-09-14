import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';

/**
 * AgentLoading —— 慢路径占位。
 *
 * 接口清单 §7：`deep_agent` 要 9~15s，且**只在 SYNTHESIZE 阶段产 token**
 * （规划/检索期间没有任何输出）。前 5~10s 界面空白是正常的，
 * 但**空白会被用户读成"卡死了"** —— 必须有占位。
 *
 * 延迟 1.2s 才出现：快到根本不需要占位时（如 kb_answer 2~3s，前几百毫秒就有 token）
 * 不要让占位闪一下。
 */
export function AgentLoading() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setShow(true), 1200);
    return () => clearTimeout(t);
  }, []);

  if (!show) return null;

  return (
    <p
      className="flex items-center gap-2 rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-2.5 text-[13px] text-[var(--color-muted)]"
      aria-live="polite"
    >
      <Sparkles className="h-3.5 w-3.5 animate-pulse text-[var(--color-accent)]" aria-hidden />
      正在检索并核对依据…
    </p>
  );
}
