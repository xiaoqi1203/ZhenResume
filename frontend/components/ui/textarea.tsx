import * as React from 'react';
import { cn } from '@/lib/utils';

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, rows = 6, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(
        'flex w-full border border-ink-300 bg-white px-3 py-2 text-sm leading-relaxed text-ink-950',
        'transition-colors duration-150 ease-snap',
        'placeholder:text-ink-400',
        'hover:border-ink-500',
        'focus:border-ink-950 focus:outline-none focus:ring-0',
        'disabled:cursor-not-allowed disabled:border-ink-200 disabled:bg-ink-50 disabled:text-ink-500',
        'resize-y',
        className,
      )}
      {...props}
    />
  );
});

export default Textarea;