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
        'flex h-10 w-full rounded-[10px] border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900',
        'transition-colors duration-200 ease-spring',
        'placeholder:text-ink-400',
        'hover:border-ink-400',
        'focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/15',
        'disabled:cursor-not-allowed disabled:border-ink-200 disabled:bg-ink-50 disabled:text-ink-500',
        'file:mr-3 file:rounded-md file:border-0 file:bg-ink-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink-700',
        className,
      )}
      {...props}
    />
  );
});

export default Input;
