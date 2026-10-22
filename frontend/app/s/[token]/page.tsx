'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ApiError, getSharedReport } from '@/lib/api';
import type { SharedReport } from '@/lib/types';
import { buttonVariants } from '@/lib/button-variants';
import { Skeleton } from '@/components/ui/skeleton';
import ShareReportView from '@/components/share-report-view';
import { ErrorBanner } from '@/components/states';

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 410 || error.code === 'SHARE_EXPIRED') {
      return '这个分享链接已过期（分享有效期 7 天），请让分享者重新生成。';
    }
    if (error.status === 404 || error.code === 'SHARE_NOT_FOUND') {
      return '这个分享链接不存在或已被撤销。';
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return '打开分享页失败，请稍后重试。';
}

function ShareSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Skeleton className="h-52 w-full" />
        <Skeleton className="h-52 w-full" />
      </div>
      <Skeleton className="h-24 w-full" />
      <p className="sr-only">分享内容加载中…</p>
    </div>
  );
}

export default function SharedReportPage() {
  const params = useParams<{ token: string }>();
  const token = typeof params?.token === 'string' ? params.token : '';

  const [report, setReport] = React.useState<SharedReport | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!token) {
      setLoading(false);
      setError('缺少分享令牌，无法打开。');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getSharedReport(token);
      setReport(data);
    } catch (err) {
      setError(describeError(err));
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [token]);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <div className="mx-auto max-w-5xl">{<ShareSkeleton />}</div>;

  if (error || !report) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 py-10">
        <ErrorBanner message={error ?? '分享内容不可用。'} onRetry={() => void load()} />
        <div className="flex flex-wrap gap-2">
          <Link href="/" className={buttonVariants()}>
            去简甄诊断我的简历
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <ShareReportView report={report} />
    </div>
  );
}
