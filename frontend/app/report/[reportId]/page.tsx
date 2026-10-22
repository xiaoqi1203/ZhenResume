'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Check, Download, PenLine, RefreshCw, Share2 } from 'lucide-react';
import { ApiError, createReportShare, downloadReportMarkdown, getReport, getTask, revokeReportShare } from '@/lib/api';
import type { Report, TaskUsage } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/lib/button-variants';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { UsageSummary } from '@/components/completion-hold';
import ReportView from '@/components/report-view';
import { ErrorBanner } from '@/components/states';
import { saveLastRun } from '@/lib/last-run';

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
  const [usage, setUsage] = React.useState<TaskUsage | null>(null);
  const [shareState, setShareState] = React.useState<'idle' | 'working' | 'copied' | 'failed'>(
    'idle',
  );
  const [exportState, setExportState] = React.useState<'idle' | 'working' | 'failed'>('idle');
  // 分享：默认结论卡（不含简历原文），用户可勾选包含原文
  const [includeRaw, setIncludeRaw] = React.useState(false);
  const [shareInfo, setShareInfo] = React.useState<{
    url: string;
    expiresAt: string | null;
    includeRaw: boolean;
  } | null>(null);
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
      // 记住这份报告：回到首页后仍能一键找回来
      if (data.task_id) {
        saveLastRun({ taskId: data.task_id, reportId: data.report_id });
      }
      // 用量（耗时 / Token / 花费）挂在任务上，不在报告里；
      // 单独取一次、失败就静默隐藏 —— 报告正文不能因为"统计信息拿不到"而报错。
      if (data.task_id) {
        void getTask(data.task_id)
          .then((task) => setUsage(task.usage ?? null))
          .catch(() => setUsage(null));
      }
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

  const handleExportMarkdown = async () => {
    if (exportState === 'working') return;
    setExportState('working');
    try {
      const { blob, filename } = await downloadReportMarkdown(reportId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      setExportState('idle');
    } catch {
      setExportState('failed');
      window.setTimeout(() => setExportState('idle'), 2400);
    }
  };

  const handleShare = async () => {
    setShareState('working');
    try {
      const result = await createReportShare(reportId, includeRaw);
      setShareInfo({
        url: result.share_url,
        expiresAt: result.expires_at,
        includeRaw: result.include_raw,
      });
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.share_url);
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

  const handleRevokeShare = async () => {
    try {
      await revokeReportShare(reportId);
    } finally {
      setShareInfo(null);
    }
  };

  const shareLabel =
    shareState === 'working'
      ? '生成中…'
      : shareState === 'copied'
        ? '链接已复制'
        : shareState === 'failed'
          ? '复制失败，请重试'
          : '生成分享链接';

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

  // 契约 §9 闭环约定：editor 来源回编辑器，upload 来源回首页重新上传。
  // 编辑器已恢复为正式入口：EDITOR_ENABLED 打开，报告页同时给出「修改后重新诊断」与
  // 「在编辑器里优化这份简历」两个入口；upload 来源没有结构化内容，仍回首页重新上传。
  //
  // 路由分工：`/editor` 是「选模板 / 选起点」的开始页，`/editor/write` 才是真正的编辑器。
  // 从报告页回来是"继续改这份简历"，所以直接深链到 write 页，避免多一次选择。
  const EDITOR_ENABLED = true;
  const fromEditor = EDITOR_ENABLED && report.resume_source === 'editor';
  const editorHref = EDITOR_ENABLED
    ? report.resume_id
      ? `/editor/write?start=resume&resumeId=${encodeURIComponent(report.resume_id)}`
      : '/editor'
    : '/';

  const actions = (
    <>
      <Button
        variant="outline"
        onClick={handleExportMarkdown}
        disabled={exportState === 'working'}
        className={exportState === 'failed' ? 'border-red-200 text-red-700' : undefined}
      >
        <Download className="h-4 w-4" aria-hidden />
        {exportState === 'working' ? '导出中…' : exportState === 'failed' ? '导出失败，重试' : '导出 Markdown'}
      </Button>
      <div className="flex flex-col items-start gap-1.5">
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
        <label className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap text-[10.5px] leading-none text-ink-500">
          <input
            type="checkbox"
            checked={includeRaw}
            onChange={(event) => setIncludeRaw(event.target.checked)}
            className="h-3 w-3 accent-ink-950"
          />
          链接包含简历原文片段
        </label>
      </div>
      <Link href={fromEditor ? editorHref : '/'} className={buttonVariants({})}>
        <RefreshCw className="h-4 w-4" aria-hidden />
        {fromEditor ? '在编辑器中修改后重新诊断' : '修改后重新诊断'}
      </Link>
      {EDITOR_ENABLED ? (
        <Link
          href={editorHref}
          className={buttonVariants({ variant: 'outline' })}
          title="把这份简历载入编辑器，改完可以再诊断一次并对比分数"
        >
          <PenLine className="h-4 w-4" aria-hidden />
          在编辑器里优化这份简历
        </Link>
      ) : null}
    </>
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {shareInfo ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border border-ink-200 bg-white px-4 py-3">
          <div className="min-w-0 text-[12px] leading-relaxed text-ink-600">
            <p>
              分享链接已生成（{shareInfo.includeRaw ? '含简历原文片段' : '仅含结论，不含简历原文'}）
              {shareInfo.expiresAt
                ? ` · 有效期至 ${new Date(shareInfo.expiresAt).toLocaleString('zh-CN', {
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}`
                : ''}
            </p>
            <p className="mt-1 break-all text-ink-950">{shareInfo.url}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void navigator.clipboard?.writeText(shareInfo.url);
              }}
            >
              再次复制
            </Button>
            <Button variant="ghost" size="sm" onClick={handleRevokeShare}>
              撤销分享
            </Button>
          </div>
        </div>
      ) : null}

      <ReportView report={report} actions={actions} />

      {/* 本次诊断的消耗：用户诊断完最常问"花了多久、花了多少钱"，
          报告页要能直接回答，而不是只在分析页闪一下。
          拿不到用量（旧数据 / 接口失败）时整块隐藏，不展示空壳。 */}
      {usage ? (
        <section>
          <div className="border-b border-ink-200 pb-3">
            <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
              本次诊断的消耗
            </h2>
          </div>
          <UsageSummary usage={usage} className="mt-px border border-ink-200" />
        </section>
      ) : null}
    </div>
  );
}
