'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Check, Download, PenLine, RefreshCw, Share2 } from 'lucide-react';
import { ApiError, getReport, getReportMarkdownUrl, getReportShare } from '@/lib/api';
import type { Report } from '@/lib/types';
import { Button } from '@/components/ui/button';
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

function ReportSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-4 w-80" />
      </div>
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-6 pt-6">
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-14 w-24" />
            <Skeleton className="h-2 w-full" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="space-y-3 p-6 pt-6">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-52 w-full" />
          </CardContent>
        </Card>
      </div>
      {[0, 1, 2].map((index) => (
        <Card key={index}>
          <CardContent className="space-y-3 p-5 pt-5">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </CardContent>
        </Card>
      ))}
      <p className="sr-only">报告加载中…</p>
    </div>
  );
}

export default function ReportPage() {
  const params = useParams<{ reportId: string }>();
  const reportId = typeof params?.reportId === 'string' ? params.reportId : '';

  const [report, setReport] = React.useState<Report | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [shareState, setShareState] = React.useState<'idle' | 'working' | 'copied' | 'failed'>(
    'idle',
  );
  const shareTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = React.useCallback(async () => {
    if (!reportId) {
      setLoading(false);
      setError('缺少报告 ID，请返回首页重新发起诊断。');
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

  React.useEffect(() => {
    return () => {
      if (shareTimer.current) clearTimeout(shareTimer.current);
    };
  }, []);

  const handleShare = async () => {
    setShareState('working');
    try {
      const result = await getReportShare(reportId);
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.share_url);
      } else {
        throw new Error('clipboard unavailable');
      }
      setShareState('copied');
      if (shareTimer.current) clearTimeout(shareTimer.current);
      shareTimer.current = setTimeout(() => setShareState('idle'), 2400);
    } catch {
      setShareState('failed');
      if (shareTimer.current) clearTimeout(shareTimer.current);
      shareTimer.current = setTimeout(() => setShareState('idle'), 2400);
    }
  };

  const shareLabel =
    shareState === 'working'
      ? '生成中…'
      : shareState === 'copied'
        ? '链接已复制'
        : shareState === 'failed'
          ? '复制失败，请重试'
          : '分享链接';

  if (loading) return <ReportSkeleton />;

  if (error || !report) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 py-8">
        <ErrorBanner message={error ?? '报告不存在或已被删除。'} onRetry={() => void load()} />
        <div className="flex flex-wrap gap-2">
          <Link href="/" className={buttonVariants()}>
            返回首页重新诊断
          </Link>
        </div>
      </div>
    );
  }

  // 契约 §9 闭环约定：editor 来源回编辑器（带 resumeId 回填原内容），upload 来源回首页重新上传
  const fromEditor = report.resume_source === 'editor';
  const editorHref = report.resume_id
    ? `/editor?resumeId=${encodeURIComponent(report.resume_id)}`
    : '/editor';

  const actions = (
    <>
      <a
        href={getReportMarkdownUrl(report.report_id)}
        download
        className={buttonVariants({ variant: 'outline', size: 'default' })}
      >
        <Download className="h-4 w-4" aria-hidden />
        导出 Markdown
      </a>
      <Button
        variant="outline"
        onClick={handleShare}
        disabled={shareState === 'working'}
        className={shareState === 'copied' ? 'border-green-200 text-green-700' : undefined}
      >
        {shareState === 'copied' ? (
          <Check className="h-4 w-4" aria-hidden />
        ) : (
          <Share2 className="h-4 w-4" aria-hidden />
        )}
        {shareLabel}
      </Button>
      <Link href={fromEditor ? editorHref : '/'} className={buttonVariants({})}>
        <RefreshCw className="h-4 w-4" aria-hidden />
        {fromEditor ? '在编辑器中修改后重新诊断' : '修改后重新诊断'}
      </Link>
      <Link
        href={editorHref}
        className={buttonVariants({ variant: 'outline' })}
        title="把这份简历载入编辑器，改完可以再诊断一次并对比分数"
      >
        <PenLine className="h-4 w-4" aria-hidden />
        在编辑器里优化这份简历
      </Link>
    </>
  );

  return (
    <div className="mx-auto max-w-5xl">
      <ReportView report={report} actions={actions} />
    </div>
  );
}
