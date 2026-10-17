import type { ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

/** 统一的红色错误提示条。 */
export function ErrorBanner({
  message,
  className,
  onRetry,
}: {
  message: string;
  className?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-wrap items-center justify-between gap-3 border border-red-500 bg-red-50 px-4 py-3 text-[13px] text-red-700',
        className,
      )}
    >
      <span className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span className="break-words">{message}</span>
      </span>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry} className="border-red-500 text-red-700">
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          重试
        </Button>
      ) : null}
    </div>
  );
}

/** 友好中文空态。 */
export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 border border-dashed border-ink-300 bg-ink-50 px-6 py-12 text-center',
        className,
      )}
    >
      <p className="text-[15px] font-bold tracking-tight text-ink-950">{title}</p>
      {description ? <p className="max-w-md text-[12px] leading-relaxed text-ink-500">{description}</p> : null}
      {action}
    </div>
  );
}

/** 卡片式加载骨架。 */
export function LoadingBlock({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={cn('space-y-3', className)} aria-busy="true" aria-live="polite">
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className={index === 0 ? 'h-6 w-1/3' : 'h-4 w-full'} />
      ))}
      <span className="sr-only">加载中…</span>
    </div>
  );
}

/** 简洁的页内 spinner + 文案。 */
export function InlineSpinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-[12px] text-ink-500">
      <span
        className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600"
        aria-hidden
      />
      {label}
    </div>
  );
}