import { redirect } from 'next/navigation';

/**
 * 旧的 3D 棱柱演示已下线：需求最终定为「竖向卡片 + translateY 分页切换」，
 * 演示页迁移到 /neotech，这里保留 302 跳转，避免旧链接 404。
 *
 * 注意：静态预渲染的页面里调用 redirect() 只会产出一个空壳页面，
 * 必须声明 force-dynamic 才会在请求时返回真正的 307 + Location。
 */
export const dynamic = 'force-dynamic';

export default function LegacyPrismDemoPage() {
  redirect('/neotech');
}
