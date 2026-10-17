import * as React from 'react';
import { cn } from '@/lib/utils';

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, type = 'text', ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      className={cn(
        'flex h-10 w-full border border-ink-300 bg-white px-3 py-2 text-sm text-ink-950',
        'transition-colors duration-150 ease-snap',
        'placeholder:text-ink-400',
        'hover:border-ink-500',
        'focus:border-ink-950 focus:outline-none focus:ring-0',
        'disabled:cursor-not-allowed disabled:border-ink-200 disabled:bg-ink-50 disabled:text-ink-500',
        'file:mr-3 file:border-0 file:bg-ink-950 file:px-3 file:py-1.5 file:text-[11px] file:font-semibold file:uppercase file:tracking-[0.06em] file:text-white',
        className,
      )}
      {...props}
    />
  );
});

export default Input;