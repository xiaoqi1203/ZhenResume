import * as React from 'react';
import { cn } from '@/lib/utils';

export type BadgeVariant =
  | 'default'
  | 'brand'
  | 'secondary'
  | 'outline'
  | 'red'
  | 'yellow'
  | 'green';

/**
 * 徽章。语义色统一用暖色系（砖红 / 赭黄 / 苔绿）。
 * 之前的 `blue` 变体从未被使用，已在本次改造中移除，避免留下不可重映射的冷色。
 */
const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: 'bg-ink-900 text-ink-50',
  brand: 'bg-brand-50 text-brand-800 ring-1 ring-inset ring-brand-200',
  secondary: 'bg-ink-100 text-ink-700',
  outline: 'border border-ink-300 text-ink-600',
  red: 'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200',
  yellow: 'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200',
  green: 'bg-green-50 text-green-700 ring-1 ring-inset ring-green-200',
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = 'secondary', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium leading-5',
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}

export default Badge;
