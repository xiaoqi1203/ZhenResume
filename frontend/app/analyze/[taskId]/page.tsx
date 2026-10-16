'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Clock, Loader2 } from 'lucide-react';
import { buttonVariants } from '@/lib/button-variants';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { StepTimeline } from '@/components/step-timeline';
import { ErrorBanner, LoadingBlock } from '@/components/states';
import { useTaskFeed } from '@/lib/use-task-feed';

const STATUS_TEXT: Record<string, string> = {
  pending: '任务排队中',
  running: '诊断进行中',
  done: '诊断完成',
  failed: '诊断失败',
};

/** 实时通道状态提示（业务状态，不是装饰） */
const FEED_MODE_TEXT: Record<string, string> = {
  connecting: '正在建立实时通道…',
  stream: '实时推送中',
  polling: '已回落到轮询（1.5s）',
};

export default function AnalyzePage() {
  const router = useRouter();
  const params = useParams<{ taskId: string }>();
  const taskId = typeof params?.taskId === 'string' ? params.taskId : '';

  const [retryToken, setRetryToken] = React.useState(0);
  const redirectedRef = React.useRef(false);

  // 默认走 SSE 实时推送；失败自动回落到 1.5s 轮询（逻辑都在 hook 里，与样式无关）
  const { task, error, loading, mode } = useTaskFeed(taskId, retryToken, (finished) => {
    if (finished.report_id && !redirectedRef.current) {
      redirectedRef.current = true;
      router.replace(`/report/${finished.report_id}`);
    }
  });

  const status = task?.status ?? 'pending';
  const running = status === 'pending' || status === 'running';
  const failed = status === 'failed';
  const progressPercent = Math.round(Math.min(1, Math.max(0, task?.progress ?? 0)) * 100);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/"
          className={buttonVariants({ variant: 'ghost', size: 'sm', className: '-ml-2' })}
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          返回首页
        </Link>
        <span className="flex items-center gap-2 font-mono text-xs text-slate-400">
          <span>{FEED_MODE_TEXT[mode]}</span>
          <span aria-hidden>·</span>
          <span>任务 ID：{taskId || '—'}</span>
        </span>
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-brand-100 bg-brand-50/60 px-4 py-3 text-sm text-brand-900">
        <Clock className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" aria-hidden />
        <p>
          诊断进行中，通常需要 20–60 秒。你可以留在本页观察每个 Agent 的实时状态，完成后会自动跳转到报告页。
        </p>
      </div>

      {error ? <ErrorBanner message={error} onRetry={() => setRetryToken((n) => n + 1)} /> : null}

      {failed ? (
        <Card className="border-red-200">
          <CardHeader>
            <CardTitle className="text-red-700">诊断失败</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-red-600">
              {task?.error || '后端未返回具体原因，请稍后重试。'}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => router.push('/')}>返回重试</Button>
              <Button
                variant="outline"
                onClick={() => {
                  // 重新建立实时通道（失败会自动回落轮询）；错误态由 hook 内部重置
                  setRetryToken((n) => n + 1);
                }}
              >
                重新查询状态
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin text-brand-600" aria-hidden />
            ) : null}
            {STATUS_TEXT[status] ?? status}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between text-xs text-slate-500">
              <span>
                当前步骤：{task?.current_step ?? '等待调度'} · 共 {task?.total_steps ?? 8} 步
              </span>
              <span className="font-mono text-slate-700">{progressPercent}%</span>
            </div>
            <Progress value={progressPercent} />
          </div>

          {loading && !task ? (
            <LoadingBlock lines={8} />
          ) : (
            <StepTimeline steps={task?.steps ?? []} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
