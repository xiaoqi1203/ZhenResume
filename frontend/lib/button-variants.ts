import { cn } from '@/lib/utils';

export type ButtonVariant =
  | 'default'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'destructive'
  | 'link'
  | 'brand';

export type ButtonSize = 'sm' | 'default' | 'lg' | 'icon';

/**
 * 按钮变体 —— 参照 image_demo/img_1.png：
 * - 主按钮 = **纯黑底白字**（参考图里 "Get tickets" / "Explore skill" 就是这个）；
 * - 次按钮 = 白底 + 1px 黑线，hover 反色（黑底白字）；
 * - 紫色只用于链接/强调，不做大面积按钮填充（保持黑白主导）。
 * - 全部变体都有 hover / active / focus-visible 三态，active 用 1px 下移模拟按压。
 */
export const BUTTON_VARIANT_CLASSES: Record<ButtonVariant, string> = {
  default:
    'border border-ink-950 bg-ink-950 text-white hover:bg-white hover:text-ink-950 active:translate-y-px focus-visible:outline-ink-950',
  brand:
    'border border-brand-500 bg-brand-500 text-white hover:bg-white hover:text-brand-600 hover:border-brand-500 active:translate-y-px focus-visible:outline-brand-600',
  secondary:
    'border border-ink-950 bg-white text-ink-950 hover:bg-ink-950 hover:text-white active:translate-y-px focus-visible:outline-ink-950',
  outline:
    'border border-ink-300 bg-white text-ink-700 hover:border-ink-950 hover:text-ink-950 active:translate-y-px focus-visible:outline-ink-950',
  ghost:
    'border border-transparent bg-transparent text-ink-600 hover:bg-ink-100 hover:text-ink-950 focus-visible:outline-ink-950',
  destructive:
    'border border-red-600 bg-red-600 text-white hover:bg-white hover:text-red-700 active:translate-y-px focus-visible:outline-red-600',
  link: 'border border-transparent text-brand-600 underline-offset-4 hover:text-brand-500 hover:underline focus-visible:outline-brand-500',
};

export const BUTTON_SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[12px]',
  default: 'h-10 px-4 text-sm',
  lg: 'h-11 px-6 text-sm',
  icon: 'h-9 w-9',
};

export const BUTTON_BASE_CLASSES =
  'inline-flex select-none items-center justify-center gap-2 whitespace-nowrap font-semibold uppercase tracking-[0.04em] transition-all duration-200 ease-snap focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-40';

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
