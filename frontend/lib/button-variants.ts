import { cn } from '@/lib/utils';

export type ButtonVariant =
  | 'default'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'destructive'
  | 'link';

export type ButtonSize = 'sm' | 'default' | 'lg' | 'icon';

/**
 * 按钮变体。
 *
 * 设计要点：
 * - 主按钮用 `brand-600`（陶土）而非 `brand-500`（珊瑚）作为填充色：白字对比度更够；
 *   `brand-500` 留给文字、图标、焦点环，保持签名色的观感。
 * - outline / ghost 以暖灰发丝边框和极轻底色表达层级，不用重阴影。
 * - 全部变体都有 hover / active / focus-visible 三态，`active:scale-[0.98]` 模拟物理按压。
 */
export const BUTTON_VARIANT_CLASSES: Record<ButtonVariant, string> = {
  default:
    'bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:bg-brand-800 focus-visible:outline-brand-600',
  secondary:
    'bg-ink-100 text-ink-800 hover:bg-ink-200 active:bg-ink-300/70 focus-visible:outline-ink-400',
  outline:
    'border border-ink-300 bg-white text-ink-700 hover:border-ink-400 hover:bg-ink-50 hover:text-ink-900 focus-visible:outline-brand-500',
  ghost:
    'text-ink-600 hover:bg-ink-100 hover:text-ink-900 focus-visible:outline-ink-400',
  destructive:
    'bg-red-600 text-white shadow-sm hover:bg-red-700 active:bg-red-800 focus-visible:outline-red-600',
  link: 'text-brand-700 underline-offset-4 hover:text-brand-800 hover:underline focus-visible:outline-brand-600',
};

export const BUTTON_SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: 'h-8 rounded-lg px-3 text-xs',
  default: 'h-10 rounded-[10px] px-4 text-sm',
  lg: 'h-11 rounded-[10px] px-6 text-sm',
  icon: 'h-9 w-9 rounded-[10px]',
};

export const BUTTON_BASE_CLASSES =
  'inline-flex select-none items-center justify-center gap-2 whitespace-nowrap font-medium transition-all duration-200 ease-spring focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 disabled:active:scale-100';

export interface ButtonVariantOptions {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}

/**
 * 生成按钮样式类名，便于 `<Link>` 等元素复用（类似 shadcn 的 buttonVariants）。
 *
 * 注意：本模块必须保持为「非 client 模块」（不要加 `'use client'`），
 * 否则函数在 Server Component 中会变成不可调用的 client reference proxy。
 */
export function buttonVariants({
  variant = 'default',
  size = 'default',
  className,
}: ButtonVariantOptions = {}): string {
  return cn(BUTTON_BASE_CLASSES, BUTTON_VARIANT_CLASSES[variant], BUTTON_SIZE_CLASSES[size], className);
}
