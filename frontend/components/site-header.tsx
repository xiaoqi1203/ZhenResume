'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { FileScan } from 'lucide-react';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { href: '/', label: '首页' },
  { href: '/editor', label: '简历编辑器' },
  { href: '/hr', label: 'HR 批量' },
  { href: '/admin', label: '管理后台' },
  { href: '/pricing', label: '定价' },
];

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 w-full border-b border-ink-200 bg-ink-50/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-6 px-4 sm:px-6">
        {/* 品牌：中文用衬线体拿编辑气质，拉丁副名用无衬线小字形成对比 */}
        <Link
          href="/"
          className="group flex shrink-0 items-center gap-2.5 rounded-lg focus-visible:outline-none"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-[9px] bg-brand-600 text-white transition-colors duration-200 ease-spring group-hover:bg-brand-700">
            <FileScan className="h-4 w-4" aria-hidden strokeWidth={1.75} />
          </span>
          <span className="font-serif text-[17px] font-semibold leading-none tracking-tight text-ink-900">
            简甄
            <span className="ml-1.5 font-sans text-[11px] font-normal tracking-wide text-ink-400">
              ZhenResume
            </span>
          </span>
        </Link>

        {/* 当前页用珊瑚色下划线指示（而非药丸底色）：信息更轻，也更编辑化 */}
        <nav className="flex items-center overflow-x-auto" aria-label="主导航">
          {NAV_ITEMS.map((item) => {
            const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'relative flex h-14 shrink-0 items-center px-3 text-[13.5px] transition-colors duration-200 ease-spring',
                  active ? 'text-ink-900' : 'text-ink-500 hover:text-ink-900',
                )}
              >
                {item.label}
                {active ? (
                  <span
                    className="absolute inset-x-2.5 bottom-0 h-[2px] rounded-full bg-brand-500"
                    aria-hidden
                  />
                ) : null}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

export default SiteHeader;
