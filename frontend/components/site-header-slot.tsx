'use client';

import { usePathname } from 'next/navigation';

/**
 * 全站顶栏的壳子：登录页要整屏铺满（背景大标题 WELCOME + 卡片居中），
 * 所以 `/login` 下不渲染 header —— 这里做一次路径判断，而不是把 header
 * 拆到每个页面里重复写。
 */
export function SiteHeaderSlot({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname?.startsWith('/login')) return null;
  return <>{children}</>;
}

export default SiteHeaderSlot;
