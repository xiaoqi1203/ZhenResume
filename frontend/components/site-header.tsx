'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { authApi } from '@/lib/auth';

/**
 * 导航项。
 * - 首页 / 编辑简历 / HR 批量 / 用户 是常规入口；
 * - 「定价」已并入「用户 → 订阅方案」，所以主导航里换成「用户」；
 *   路由 `app/pricing` 保留，/pricing 仍可直接访问（对外宣传页 / 分享链接不受影响）；
 * - 「管理后台」是**管理员专属**：非管理员既看不到入口，直接访问 /admin 也会被
 *   AuthGuard + 后端 `/api/v1/admin/*` 的 require_admin 双重拦住；
 */
interface NavItem {
  href: string;
  label: string;
  /** 仅管理员可见；默认 false */
  adminOnly?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: '首页' },
  { href: '/editor', label: '编辑简历' },
  { href: '/hr', label: 'HR 批量' },
  { href: '/admin', label: '管理后台', adminOnly: true },
  { href: '/user', label: '用户' },
];

/**
 * 顶部导航 —— 参照 image_demo/img_1.png：
 * 左 logo（黑色方块字标）、右横向文字导航、当前页用**黑色下划线**标出，
 * 整条 header 只有一条 1px 发丝线，不用阴影、不用药丸底色。
 */
export function SiteHeader() {
  const pathname = usePathname();
  // 角色存在 localStorage。首帧（SSR/水合）先按「非管理员」渲染，避免把后台入口
  // 闪给普通用户；登录态变化会伴随路由变化，下面按 pathname 重读。
  const [isAdmin, setIsAdmin] = React.useState(false);

  React.useEffect(() => {
    setIsAdmin(authApi.getSession()?.account.role === 'admin');
  }, [pathname]);

  const items = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-ink-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-[62px] max-w-[1180px] items-stretch justify-between gap-6 px-5 sm:px-8">
        {/* 品牌：黑底白字方块 + 名称，参考图里 logo 是纯黑几何图形 */}
        <Link href="/" className="group flex shrink-0 items-center gap-2.5 focus-visible:outline-none">
          <span className="flex h-7 w-7 items-center justify-center bg-ink-950 text-[15px] font-extrabold leading-none text-white transition-colors duration-200 group-hover:bg-brand-500">
            简
          </span>
          <span className="flex items-baseline gap-1.5">
            <span className="text-[17px] font-extrabold leading-none tracking-tighter text-ink-950">
              简甄
            </span>
            <span className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink-600">
              ZhenResume
            </span>
          </span>
        </Link>

        {/* 导航：当前页用 2px 黑色下划线指示，与 header 底边对齐 */}
        <nav className="flex items-stretch overflow-x-auto" aria-label="主导航">
          {items.map((item) => {
            const active = item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'relative flex shrink-0 items-center px-3.5 text-[13px] font-medium transition-colors duration-200 sm:px-4',
                  active ? 'text-ink-950' : 'text-ink-400 hover:text-ink-950',
                )}
              >
                {item.label}
                {active ? (
                  <span className="absolute inset-x-0 bottom-0 h-[2px] bg-ink-950" aria-hidden />
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