import type { Metadata } from 'next';
import LoginCard from '@/components/auth/login-card';

export const metadata: Metadata = {
  title: '登录 · 简甄 ZhenResume',
  description: '登录或创建账号，继续你的简历诊断与改写。',
};

/**
 * 登录页（/login）
 * ------------------------------------------------------------------
 * - 公开路由：不需要登录即可访问；
 * - `?next=<encoded path>` 用于「被拦截后回到原页面」：
 *     未登录访问 /hr → /login?next=%2Fhr，登录成功后回 /hr，
 *     而不是固定跳首页，更不是「用户」页；
 * - 没有 next 时才回首页；
 * - 页面本身是 fixed 全屏铺满，所以这里不渲染全站的 header / footer。
 */

/**
 * 只接受「站内绝对路径」，防止开放重定向：
 * - 必须以单个 `/` 开头（`//evil.com`、`/\evil.com` 都挡掉）
 * - 不能是带协议的绝对 URL
 */
function safeNextPath(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null;
  const value = raw.trim();
  if (!value.startsWith('/')) return null;
  if (value.startsWith('//') || value.startsWith('/\\')) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return null;
  return value;
}

export default function LoginPage({ searchParams }: { searchParams?: { next?: string | string[] } }) {
  const raw = Array.isArray(searchParams?.next) ? searchParams?.next[0] : searchParams?.next;
  const nextPath = safeNextPath(raw) ?? '/';
  return <LoginCard nextPath={nextPath} />;
}
