import { AlertTriangle, Check, Minus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { fmtDeviation, type DeviationLevel } from '@/lib/inquiry';

/**
 * DeviationBadge —— 偏离度高亮（设计文档 §4.4）。
 *
 * 规范要点：状态色**必须**配图标 + 文字（`icon + label`），绝不靠颜色单独表意 ——
 * 色觉障碍、灰度打印、强制高对比模式下颜色都会失效，图标和文字不会。
 */
export function DeviationBadge({ level, rate }: { level: DeviationLevel; rate: number }) {
  if (level === 'over') {
    return (
      <Badge variant="danger" className="whitespace-nowrap">
        <AlertTriangle className="h-3 w-3" aria-hidden />
        超阈值 {fmtDeviation(rate)}
      </Badge>
    );
  }
  if (level === 'above') {
    return (
      <Badge variant="warning" className="whitespace-nowrap">
        <Minus className="h-3 w-3" aria-hidden />
        高于基准 {fmtDeviation(rate)}
      </Badge>
    );
  }
  return (
    <Badge variant="success" className="whitespace-nowrap">
      <Check className="h-3 w-3" aria-hidden />
      低于基准 {fmtDeviation(rate)}
    </Badge>
  );
}
