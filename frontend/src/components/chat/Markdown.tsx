import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { citationNumberFromHref, linkifyCitations } from '@/lib/chat';

/**
 * Markdown —— 回答正文渲染。
 *
 * `【来源N】` 先被转成 `[【来源N】](#cite-N)`，再由这里换成**可点击控件** ——
 * 它不该是一段死文字，点它应当能跳到对应引用。
 */
export function Markdown({
  children,
  onCitationClick,
}: {
  children: string;
  onCitationClick?: (n: number) => void;
}) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: kids }) => {
            const n = href ? citationNumberFromHref(href) : null;
            if (n !== null) {
              if (!onCitationClick) return <span className="font-medium">{kids}</span>;
              return (
                <button
                  type="button"
                  onClick={() => onCitationClick(n)}
                  className="mx-0.5 inline-flex items-center rounded-[5px] bg-[var(--color-accent-soft)] px-1.5 py-px align-baseline font-mono text-[11px] font-medium text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-accent)] hover:text-[var(--color-accent-ink)]"
                >
                  {kids}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {kids}
              </a>
            );
          },
        }}
      >
        {linkifyCitations(children)}
      </ReactMarkdown>
    </div>
  );
}
