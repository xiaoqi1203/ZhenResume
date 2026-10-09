import Link from 'next/link';
import { FileQuestion } from 'lucide-react';
import { buttonVariants } from '@/lib/button-variants';

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-100 text-slate-400">
        <FileQuestion className="h-6 w-6" aria-hidden />
      </span>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">404 · 页面不存在</h1>
      <p className="max-w-md text-sm text-slate-500">
        你访问的页面可能已被移除、链接拼写有误，或对应的报告已经过期。
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Link href="/" className={buttonVariants({ className: 'px-4' })}>
          返回首页重新诊断
        </Link>
        <Link
          href="/pricing"
          className="inline-flex h-10 items-center justify-center rounded-lg border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
        >
          查看定价
        </Link>
      </div>
    </div>
  );
}
