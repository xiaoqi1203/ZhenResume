import { cn } from '@/lib/utils';
import type { RiskLevel } from '@/lib/types';

const LABELS: Record<RiskLevel, string> = {
  red: '高风险',
  yellow: '需优化',
  green: '表现良好',
};

const CLASSES: Record<RiskLevel, string> = {
  red: 'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200',
  yellow: 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200',
  green: 'bg-green-50 text-green-700 ring-1 ring-inset ring-green-200',
};

const DOTS: Record<RiskLevel, string> = {
  red: 'bg-red-500',
  yellow: 'bg-amber-500',
  green: 'bg-green-500',
};

export interface RiskBadgeProps {
  level: RiskLevel | null | undefined;
  className?: string;
  /** 覆盖默认中文文案 */
  label?: string;
  showDot?: boolean;
}

export function RiskBadge({ level, className, label, showDot = true }: RiskBadgeProps) {
  if (!level) {
    return (
      <span
        className={cn(
          'inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium leading-5 text-slate-500',
          className,
        )}
      >
        待评估
      </span>
    );
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium leading-5',
        CLASSES[level],
        className,
      )}
    >
      {showDot ? <span className={cn('h-1.5 w-1.5 rounded-full', DOTS[level])} aria-hidden /> : null}
      {label ?? LABELS[level]}
    </span>
  );
}

export const RISK_LEVEL_LABELS = LABELS;

export default RiskBadge;
