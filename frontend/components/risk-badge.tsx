import { cn } from '@/lib/utils';
import type { RiskLevel } from '@/lib/types';

const LABELS: Record<RiskLevel, string> = {
  red: '高风险',
  yellow: '需优化',
  green: '表现良好',
};

const CLASSES: Record<RiskLevel, string> = {
  red: 'border border-red-500 text-red-700',
  yellow: 'border border-amber-500 text-amber-700',
  green: 'border border-green-600 text-green-700',
};

const DOTS: Record<RiskLevel, string> = {
  red: 'bg-red-500',
  yellow: 'bg-amber-500',
  green: 'bg-green-600',
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
          'inline-flex items-center border border-ink-300 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase leading-5 tracking-[0.08em] text-ink-500',
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
        'inline-flex items-center gap-1.5 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase leading-5 tracking-[0.08em]',
        CLASSES[level],
        className,
      )}
    >
      {showDot ? <span className={cn('h-1.5 w-1.5', DOTS[level])} aria-hidden /> : null}
      {label ?? LABELS[level]}
    </span>
  );
}

export const RISK_LEVEL_LABELS = LABELS;

export default RiskBadge;
