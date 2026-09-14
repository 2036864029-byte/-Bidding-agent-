import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { UploadedFileState } from '@/services/api';

/**
 * FileChips —— 已上传文件（异步解析状态一目了然）。
 *
 * 解析是异步的：chips 上必须能看出「还在解析 / 好了 / 失败了」，
 * 否则用户会对着一个不知道能不能用的文件提问。
 */
export function FileChips({
  files,
  onRemove,
}: {
  files: UploadedFileState[];
  onRemove: (id: string) => void;
}) {
  if (!files.length) return null;

  return (
    <ul className="flex flex-wrap gap-1.5">
      {files.map((f) => {
        const busy = f.status === 'pending' || f.status === 'processing';
        const error = f.status === 'error';
        return (
          <li
            key={f.id}
            className={cn(
              'flex max-w-[16rem] items-center gap-1.5 rounded-full border py-1 pl-2.5 pr-1.5 text-[12px]',
              error
                ? 'border-[oklch(85%_0.08_25)] bg-[oklch(97%_0.02_25)] text-[var(--color-danger)]'
                : 'border-[var(--color-rule-2)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)]'
            )}
            title={error ? f.error : f.preview}
          >
            {busy && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[var(--color-accent)]" aria-hidden />}
            {!busy && !error && <CheckCircle2 className="h-3 w-3 shrink-0 text-[var(--color-success)]" aria-hidden />}
            {error && <AlertCircle className="h-3 w-3 shrink-0" aria-hidden />}

            <span className="truncate">{f.name}</span>

            <span className="shrink-0 text-[11px] text-[var(--color-neutral)]">
              {busy ? '解析中' : error ? '失败' : f.chunkCount ? `${f.chunkCount} 块` : '就绪'}
            </span>

            <button
              type="button"
              onClick={() => onRemove(f.id)}
              aria-label={`移除 ${f.name}`}
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-3)] hover:text-[var(--color-ink)]"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
