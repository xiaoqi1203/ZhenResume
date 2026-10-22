'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ShieldAlert } from 'lucide-react';
import { authApi } from '@/lib/auth';

/**
 * 登录 / 角色守卫（客户端）
 * ------------------------------------------------------------------
 * 用法：把需要登录的页面内容包起来：
 *
 *   <AuthGuard>                 // 只要求登录
 *     <HrPage />
 *   </AuthGuard>
 *
 *   <AuthGuard requiredRole="admin">   // 还要求是管理员
 *     <AdminPage />
 *   </AuthGuard>
 *
 * 行为：
 * - 未登录 → 跳 `/login?next=<当前路径+查询串>`，登录成功后**回到原页面**；
 * - 已登录但角色不匹配 → 停留在本页显示「无权访问」，**不跳登录页**
 *   （跳登录页会让用户反复登录，误以为自己账号有问题）；
 * - 已登录且角色匹配 → 直接渲染 children；
 * - 首帧渲染占位，避免「先闪一下页面再跳走」。
 *
 * ⚠️ 这层只是**体验层**：真正的权限边界必须在后端（`api/deps.py::require_admin`）。
 * 前端 localStorage 里的 role 可以被用户改，但后端每次请求都会查 DB 的 users.role。
 *
 * 为什么放客户端而不是 middleware：登录态存在 localStorage（`lib/auth.ts`），
 * 服务端读不到；等 token 迁到 httpOnly cookie 后可以换成 middleware 做服务端重定向。
 */
export function AuthGuard({
  children,
  requiredRole,
}: {
  children: React.ReactNode;
  requiredRole?: string;
}) {
  const router = useRouter();
  const [state, setState] = React.useState<'checking' | 'ok' | 'forbidden'>('checking');

  React.useEffect(() => {
    const session = authApi.getSession();
    if (!session) {
      // 带上当前地址，登录后原路返回
      const here = window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(here)}`);
      return;
    }
    if (requiredRole && session.account.role !== requiredRole) {
      setState('forbidden');
      return;
    }
    setState('ok');
  }, [router, requiredRole]);

  if (state === 'checking') {
    return (
      <div className="py-24 text-center text-sm text-ink-500" role="status" aria-live="polite">
        正在校验登录状态…
      </div>
    );
  }

  if (state === 'forbidden') {
    return (
      <div className="mx-auto max-w-lg py-16 text-center" role="alert">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-ink-200 bg-white">
          <ShieldAlert className="h-5 w-5 text-ink-500" aria-hidden />
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-950">无权访问</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-500">
          这个页面只对管理员开放。你的账号没有该权限，请返回首页继续使用简历诊断。
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-md bg-ink-950 px-4 text-[13px] font-medium text-white transition-colors hover:bg-ink-800"
          >
            返回首页
          </Link>
          <Link
            href="/user"
            className="inline-flex h-9 items-center rounded-md border border-ink-300 px-4 text-[13px] font-medium text-ink-700 transition-colors hover:border-ink-950 hover:text-ink-950"
          >
            账号中心
          </Link>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export default AuthGuard;