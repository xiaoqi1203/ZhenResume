'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

interface TabsContextValue {
  value: string;
  setValue: (value: string) => void;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabsContext(component: string): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) throw new Error(`<${component}> 必须放在 <Tabs> 内部使用`);
  return ctx;
}

export interface TabsProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}

export function Tabs({
  value,
  defaultValue = '',
  onValueChange,
  className,
  children,
  ...props
}: TabsProps) {
  const [innerValue, setInnerValue] = React.useState(defaultValue);
  const isControlled = value !== undefined;
  const current = isControlled ? value : innerValue;

  const setValue = React.useCallback(
    (next: string) => {
      if (!isControlled) setInnerValue(next);
      onValueChange?.(next);
    },
    [isControlled, onValueChange],
  );

  return (
    <TabsContext.Provider value={{ value: current, setValue }}>
      <div className={cn('w-full', className)} {...props}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

/**
 * 标签栏 —— 参照参考图顶部 "Conference / Reading app / Product launch"：
 * 无容器底色、无圆角，当前项用**黑色下划线**指示。
 */
export function TabsList({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="tablist"
      className={cn('flex flex-wrap items-stretch gap-6 border-b border-ink-200', className)}
      {...props}
    />
  );
}

export interface TabsTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

export function TabsTrigger({ value, className, children, ...props }: TabsTriggerProps) {
  const { value: current, setValue } = useTabsContext('TabsTrigger');
  const active = current === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-state={active ? 'active' : 'inactive'}
      onClick={() => setValue(value)}
      className={cn(
        'relative -mb-px inline-flex items-center justify-center whitespace-nowrap border-b-2 pb-2.5 pt-1 text-[13px] font-medium transition-colors duration-150',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-950',
        active
          ? 'border-ink-950 text-ink-950'
          : 'border-transparent text-ink-400 hover:text-ink-950',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string;
}

export function TabsContent({ value, className, children, ...props }: TabsContentProps) {
  const { value: current } = useTabsContext('TabsContent');
  if (current !== value) return null;
  return (
    <div role="tabpanel" className={cn('mt-6 animate-fade-in', className)} {...props}>
      {children}
    </div>
  );
}

export default Tabs;