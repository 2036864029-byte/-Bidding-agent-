import { AlertTriangle, OctagonAlert, ShieldCheck } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { riskSeverity } from '@/lib/supplier';

/**
 * RiskTags —— 风险预警标签。
 *
 * 规范要点：状态色**必须**配图标 + 文字（`icon + label`），绝不靠颜色单独表意；
 * 无风险时也显式给一个正向状态，而不是留白（留白会被读成「没查」）。
 */
export function RiskTags({ tags }: { tags: string[] }) {
  if (!tags.length) {
    return (
      <Badge variant="success">
        <ShieldCheck className="h-3 w-3" aria-hidden />
        无风险记录
      </Badge>
    );
  }

  return (
    <>
      {tags.map((t) =>
        riskSeverity(t) === 'critical' ? (
          <Badge key={t} variant="danger">
            <OctagonAlert className="h-3 w-3" aria-hidden />
            {t}
          </Badge>
        ) : (
          <Badge key={t} variant="warning">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            {t}
          </Badge>
        )
      )}
    </>
  );
}
