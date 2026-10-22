'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { buttonVariants } from '@/lib/button-variants';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { StepTimeline } from '@/components/step-timeline';
import { CompletionHold, UsageSummary, formatDuration } from '@/components/completion-hold';
import { ErrorBanner, LoadingBlock } from '@/components/states';
import { FullPageDeck } from '@/components/full-page-deck/full-page-deck';
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

/**
 * 完成后停留多久再跳报告页。
 *
 * 为什么是 8 秒：三个数字（耗时 / Token / 花费）要 1–2 秒才看得完，但要给"想细看的人"
 * 留出反应时间。用户回看自己的诊断时，最想确认的就是"这几十秒到底花了多少钱"，
 * 8 秒够读完，又不会让人觉得被卡住（页面上随时可以点「立即查看报告」跳过）。
 */
const HOLD_SECONDS = 8;

const SLIDE_LABELS = ['任务状态', '执行流程', '结果'];

export default function AnalyzePage() {
  const router = useRouter();
  const params = useParams<{ taskId: string }>();
  const taskId = typeof params?.taskId === 'string' ? params.taskId : '';

  const [retryToken, setRetryToken] = React.useState(0);
  const [paused, setPaused] = React.useState(false);
  const [remaining, setRemaining] = React.useState(HOLD_SECONDS);
  const [slide, setSlide] = React.useState(0);

  // 只在「首次进入这条任务」时倒计时：从报告页点返回时不重复停留
  const holdStartedRef = React.useRef(false);

  // 默认走 SSE 实时推送；失败自动回落到 1.5s 轮询（逻辑都在 hook 里，与样式无关）
  const { task, error, loading, mode } = useTaskFeed(taskId, retryToken);

  const status = task?.status ?? 'pending';
  const running = status === 'pending' || status === 'running';
  const done = status === 'done';
  const failed = status === 'failed';
  const progressPercent = Math.round(Math.min(1, Math.max(0, task?.progress ?? 0)) * 100);
  const reportId = task?.report_id;

  const goToReport = React.useCallback(() => {
    if (!reportId) return;
    router.replace(`/report/${reportId}`);
  }, [reportId, router]);

  // 完成即开始倒计时（而不是立刻跳走），让耗时 / 花费看得见
  React.useEffect(() => {
    if (!done || !reportId || holdStartedRef.current) return;
    holdStartedRef.current = true;
    setRemaining(HOLD_SECONDS);
  }, [done, reportId]);

  React.useEffect(() => {
    if (!done || !reportId || paused) return;
    if (remaining <= 0) {
      goToReport();
      return;
    }
    const timer = setTimeout(() => setRemaining((n) => Math.max(0, n - 1)), 1000);
    return () => clearTimeout(timer);
  }, [done, reportId, paused, remaining, goToReport]);

  // 运行中的「已用时」：每 200ms 刷新一次，让「12.4 秒」的小数真的在动
  // （原来 1s 一次，toFixed(1) 永远显示 .0）。
  const [elapsedMs, setElapsedMs] = React.useState(0);
  const startedAtRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (task?.created_at) {
      const parsed = Date.parse(task.created_at);
      if (Number.isFinite(parsed)) startedAtRef.current = parsed;
    }
  }, [task?.created_at]);

  React.useEffect(() => {
    if (!running) return;
    const tick = () => {
      // 拿不到服务端 created_at 时（旧后端）退化为"从进入本页开始计"
      const base = startedAtRef.current ?? (startedAtRef.current = Date.now());
      setElapsedMs(Math.max(0, Date.now() - base));
    };
    tick();
    const timer = setInterval(tick, 200);
    return () => clearInterval(timer);
  }, [running]);

  // 任务完成 / 失败时，自动推进到「结果」屏（否则用户停在前面几屏根本看不到停留页）
  const revealedRef = React.useRef(false);
  React.useEffect(() => {
    if (revealedRef.current) return;
    if ((done && reportId) || failed) {
      revealedRef.current = true;
      setSlide(2);
    }
  }, [done, reportId, failed]);

  const hintText = running
    ? '完整诊断通常需要 30–90 秒（8 个 Agent 依次分析，其中知识库证据检索最耗时）。'
    : done
      ? '诊断已完成，结果页会展示本次消耗并自动打开完整报告。'
      : failed
        ? '任务失败，可以返回首页重新提交，或稍后重试查询状态。'
        : '任务排队中，通常几秒内开始。';

  const slides = [
    // ---------------- 01 / 任务状态 ----------------
    <div key="status" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/"
          className={buttonVariants({ variant: 'ghost', size: 'sm', className: '-ml-2' })}
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          返回首页
        </Link>
        <span className="flex items-center gap-2 font-mono text-[11px] text-ink-500">
          <span>{FEED_MODE_TEXT[mode]}</span>
          <span aria-hidden>·</span>
          <span>任务 ID：{taskId || '—'}</span>
        </span>
      </div>

      {error ? <ErrorBanner message={error} onRetry={() => setRetryToken((n) => n + 1)} /> : null}

      <section className="border border-ink-200 bg-white">
        <div className="flex flex-wrap items-start justify-between gap-6 border-b border-ink-200 px-6 py-7">
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-ink-500">
              单份诊断 / Live
            </p>
            <h1 className="mt-2 flex items-center gap-3 text-[34px] font-extrabold leading-none tracking-tightest text-ink-950 sm:text-[44px]">
              {running ? (
                <Loader2 className="h-7 w-7 animate-spin text-brand-600" aria-hidden />
              ) : null}
              {STATUS_TEXT[status] ?? status}
            </h1>
            <p className="mt-3 text-[12.5px] leading-relaxed text-ink-600">
              当前步骤：{task?.current_step ?? '等待调度'} · 共 {task?.total_steps ?? 8} 步
            </p>
          </div>
          <div className="text-right">
            <p className="stat-value">
              {progressPercent}
              <span className="ml-1 text-[20px] font-semibold text-ink-400">%</span>
            </p>
            <p className="stat-label mt-1.5">Progress</p>
          </div>
        </div>

        <div className="px-6 py-6">
          <Progress value={progressPercent} />

          <div className="mt-6 grid gap-px border border-ink-200 bg-ink-200 sm:grid-cols-3">
            <div className="bg-white px-4 py-3.5">
              <p className="stat-label">已用时</p>
              <p className="tnum mt-1.5 text-[20px] font-extrabold leading-none tracking-tight text-ink-950">
                {running ? formatDuration(elapsedMs) : '—'}
              </p>
            </div>
            <div className="bg-white px-4 py-3.5">
              <p className="stat-label">Token</p>
              <p className="tnum mt-1.5 text-[20px] font-extrabold leading-none tracking-tight text-ink-950">
                {task?.usage?.tokens != null ? task.usage.tokens.toLocaleString('zh-CN') : '—'}
              </p>
            </div>
            <div className="bg-white px-4 py-3.5">
              <p className="stat-label">花费</p>
              <p className="tnum mt-1.5 text-[20px] font-extrabold leading-none tracking-tight text-ink-950">
                {task?.usage?.cost != null ? `¥${task.usage.cost.toFixed(4)}` : '—'}
              </p>
            </div>
          </div>

          <p className="mt-5 text-[12px] leading-relaxed text-ink-500">{hintText}</p>
        </div>
      </section>

      {loading && !task ? <LoadingBlock lines={3} /> : null}
    </div>,

    // ---------------- 02 / 执行流程 ----------------
    <div key="pipeline" className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="stat-label">Pipeline</p>
          <h2 className="mt-1.5 text-[26px] font-extrabold leading-none tracking-tightest text-ink-950 sm:text-[32px]">
            8 个 Agent 依次执行
          </h2>
        </div>
        <p className="tnum text-[12px] font-semibold text-ink-500">
          {task?.current_step ?? '等待调度'} / {task?.total_steps ?? 8}
        </p>
      </div>

      <div className="border border-ink-200 bg-white px-5 py-5 sm:px-6">
        {loading && !task ? (
          <LoadingBlock lines={8} />
        ) : (
          <StepTimeline steps={task?.steps ?? []} />
        )}
      </div>
    </div>,

    // ---------------- 03 / 结果 ----------------
    <div key="result" className="space-y-5">
      {done && reportId ? (
        <CompletionHold
          usage={task?.usage}
          remainingSeconds={remaining}
          totalSeconds={HOLD_SECONDS}
          paused={paused}
          onGoNow={goToReport}
          onTogglePause={() => setPaused((value) => !value)}
        />
      ) : null}

      {failed ? (
        <section className="border border-red-200 bg-white">
          <div className="border-b border-red-100 bg-red-50 px-6 py-5">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-red-700">Failed</p>
            <h2 className="mt-1.5 text-[26px] font-extrabold leading-tight tracking-tightest text-ink-950">
              诊断失败
            </h2>
          </div>
          <div className="space-y-4 px-6 py-6">
            <p className="text-[13px] leading-relaxed text-red-700">
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
          </div>
        </section>
      ) : null}

      {running || (done && !reportId) ? (
        <section className="border border-ink-200 bg-white">
          <div className="border-b border-ink-200 bg-ink-50 px-6 py-5">
            <p className="stat-label">Results</p>
            <h2 className="mt-1.5 text-[26px] font-extrabold leading-tight tracking-tightest text-ink-950">
              诊断还在进行中
            </h2>
          </div>
          <div className="px-6 py-6">
            <p className="max-w-xl text-[13px] leading-relaxed text-ink-600">
              完成后这一页会展示本次耗时、Token 与花费，并自动打开完整报告。
              可以用 ↑ 或底部进度点回到「执行流程」，查看 8 个 Agent 的实时状态。
            </p>
            {task?.usage ? (
              <div className="mt-6">
                <UsageSummary usage={task.usage} />
              </div>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>,
  ];

  return (
    <FullPageDeck
      eyebrow="单份诊断"
      ariaLabel="单份简历诊断流程"
      slideLabels={SLIDE_LABELS}
      slides={slides}
      index={slide}
      onIndexChange={setSlide}
    />
  );
}

