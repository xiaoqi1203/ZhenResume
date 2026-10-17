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
 * 徽章 —— 参照参考图：**直角 + 细线 + 小字号大写**，不用圆角药丸、不用大面积色底。
 * 语义色只体现在文字与 1px 边框上，避免整页彩色噪点。
 */
const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: 'bg-ink-950 text-white border border-ink-950',
  brand: 'bg-ink-100 text-brand-700 border border-brand-500',
  secondary: 'bg-ink-100 text-ink-700 border border-ink-200',
  outline: 'bg-white text-ink-600 border border-ink-300',
  red: 'bg-white text-red-700 border border-red-500',
  yellow: 'bg-white text-amber-700 border border-amber-500',
  green: 'bg-white text-green-700 border border-green-600',
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = 'secondary', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase leading-5 tracking-[0.08em]',
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  );
}

export default Badge;