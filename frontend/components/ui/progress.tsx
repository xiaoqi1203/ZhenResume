import * as React from 'react';
import { cn } from '@/lib/utils';

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 0–100 */
  value?: number;
  indicatorClassName?: string;
}

/** 进度条：直角、通栏细线，填充默认纯黑（参考图的黑白对比） */
export function Progress({ className, value = 0, indicatorClassName, ...props }: ProgressProps) {
  const clamped = Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
      className={cn('h-1 w-full overflow-hidden bg-ink-200', className)}
      {...props}
    >
      <div
        className={cn(
          'h-full bg-ink-950 transition-[width] duration-500 ease-spring',
          indicatorClassName,
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export default Progress;