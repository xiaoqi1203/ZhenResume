'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Eye } from 'lucide-react';
import { ApiError, getReport } from '@/lib/api';
import type { Report } from '@/lib/types';
import { buttonVariants } from '@/lib/button-variants';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import ReportView from '@/components/report-view';
import { ErrorBanner } from '@/components/states';

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

function ShareSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-7 w-72" />
      <Skeleton className="h-4 w-96" />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
      <p className="sr-only">报告加载中…</p>
    </div>
  );
}

export default function SharePage() {
  const params = useParams<{ reportId: string }>();
  const reportId = typeof params?.reportId === 'string' ? params.reportId : '';

  const [report, setReport] = React.useState<Report | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!reportId) {
      setLoading(false);
      setError('缺少报告 ID，无法打开分享页。');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await getReport(reportId);
      setReport(data);
    } catch (err) {
      setError(describeError(err));
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [reportId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <ShareSkeleton />;

  if (error || !report) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 py-8">
        <ErrorBanner message={error ?? '报告不存在或分享已过期。'} onRetry={() => void load()} />
        <Link href="/" className={buttonVariants({ variant: 'outline' })}>
          去简甄诊断我的简历
        </Link>
      </div>
    );
  }

  const banner = (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-100 px-4 py-3 text-sm text-slate-600">
      <span className="flex items-center gap-2">
        <Eye className="h-4 w-4 text-slate-400" aria-hidden />
        只读分享：本页仅展示诊断结果，不含导出与分享操作。
      </span>
      <Link href="/" className={buttonVariants({ variant: 'link', size: 'sm' })}>
        我也想诊断简历 →
      </Link>
    </div>
  );

  return (
    <div className="mx-auto max-w-5xl">
      <ReportView report={report} banner={banner} />
    </div>
  );
}
