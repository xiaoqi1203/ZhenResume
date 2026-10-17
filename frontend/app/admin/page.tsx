'use client';

import * as React from 'react';
import {
  Activity,
  BarChart3,
  CheckCircle2,
  Coins,
  Gauge,
  ShieldCheck,
  Star,
  XCircle,
} from 'lucide-react';
import { ApiError, getAdminMetrics } from '@/lib/api';
import type { AdminMetrics } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ErrorBanner } from '@/components/states';
import { STEP_LABELS } from '@/components/step-timeline';

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

function formatLatency(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatNumber(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return '—';
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  emphasis?: boolean;
}

function StatCard({ label, value, hint, icon: Icon, emphasis = false }: StatCardProps) {
  return (
    <Card className={emphasis ? 'border-ink-950 bg-ink-50' : undefined}>
      <CardContent className="space-y-1.5 p-4 pt-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-slate-500">{label}</p>
          <Icon className={emphasis ? 'h-4 w-4 text-brand-600' : 'h-4 w-4 text-slate-400'} />
        </div>
        <p
          className={
            emphasis
              ? 'text-2xl font-semibold tabular-nums text-brand-700'
              : 'text-2xl font-semibold tabular-nums text-slate-900'
          }
        >
          {value}
        </p>
        {hint ? <p className="text-xs text-slate-400">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

function AdminSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true">
      <Skeleton className="h-7 w-40" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-24 w-full" />
        ))}
      </div>
      <Skeleton className="h-64 w-full" />
      <p className="sr-only">指标加载中…</p>
    </div>
  );
}

export default function AdminPage() {
  const [metrics, setMetrics] = React.useState<AdminMetrics | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMetrics(await getAdminMetrics());
    } catch (err) {
      setError(describeError(err));
      setMetrics(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="space-y-2">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            管理后台
          </h1>
          <p className="text-sm text-slate-600">任务运行质量、成本与公平性指标。</p>
        </div>
        <AdminSkeleton />
      </div>
    );
  }

  if (error || !metrics) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 py-8">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
          管理后台
        </h1>
        <ErrorBanner message={error ?? '暂无指标数据。'} onRetry={() => void load()} />
        <Button variant="outline" onClick={() => void load()}>
          重新加载
        </Button>
      </div>
    );
  }

  const successRate =
    metrics.tasks_total > 0 ? (metrics.tasks_done / metrics.tasks_total) * 100 : 0;
  const fairnessPercent = metrics.fairness_pass_rate * 100;
  const costPerReport = metrics.cost_per_report || 0;
  const costBudget = 0.5; // aaa.md §7：单份报告 API 成本 ≤ 0.5 元
  const costRatio = Math.min(100, Math.max(0, (costPerReport / costBudget) * 100));

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            管理后台
          </h1>
          <p className="text-sm text-slate-600">
            任务运行质量、LLM 成本与公平性检查指标，数据来自 <code className="font-mono text-xs">GET /api/v1/admin/metrics</code>。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          刷新数据
        </Button>
      </header>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard label="任务总数" value={formatNumber(metrics.tasks_total)} icon={Activity} />
        <StatCard
          label="成功任务"
          value={formatNumber(metrics.tasks_done)}
          hint={`成功率 ${successRate.toFixed(1)}%`}
          icon={CheckCircle2}
        />
        <StatCard
          label="失败任务"
          value={formatNumber(metrics.tasks_failed)}
          icon={XCircle}
        />
        <StatCard
          label="报告总数"
          value={formatNumber(metrics.reports_total)}
          icon={BarChart3}
        />
        <StatCard
          label="平均耗时"
          value={formatLatency(metrics.avg_latency_ms)}
          hint="单份报告端到端"
          icon={Gauge}
        />
        <StatCard
          label="Token 总量"
          value={formatNumber(metrics.tokens_total)}
          icon={Activity}
        />
        <StatCard
          label="总成本"
          value={`¥ ${formatNumber(metrics.cost_total, 2)}`}
          icon={Coins}
        />
        <StatCard
          label="平均评分"
          value={
            metrics.feedback_avg_rating > 0 ? metrics.feedback_avg_rating.toFixed(1) : '—'
          }
          hint="用户反馈均分（5 分制）"
          icon={Star}
        />
      </section>

      {/* 强调：成本与公平性 */}
      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="border-ink-950">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-brand-800">
              <Coins className="h-4 w-4" aria-hidden />
              单份成本
            </CardTitle>
            <p className="text-sm text-slate-500">
              目标 ≤ ¥{costBudget.toFixed(2)} / 份（aaa.md 成功指标）
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-3xl font-semibold tabular-nums text-brand-700">
              ¥ {formatNumber(costPerReport, 3)}
            </p>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={
                  costRatio <= 80
                    ? 'h-full rounded-full bg-green-500'
                    : costRatio <= 100
                      ? 'h-full rounded-full bg-amber-500'
                      : 'h-full rounded-full bg-red-500'
                }
                style={{ width: `${costRatio}%` }}
              />
            </div>
            <p className="text-xs text-slate-500">
              已使用预算的 {costRatio.toFixed(1)}%
              {costRatio <= 80 ? ' · 成本健康' : costRatio <= 100 ? ' · 接近上限' : ' · 已超出目标'}
            </p>
          </CardContent>
        </Card>

        <Card className="border-green-200">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-green-800">
              <ShieldCheck className="h-4 w-4" aria-hidden />
              公平性通过率
            </CardTitle>
            <p className="text-sm text-slate-500">目标 100%（不因性别、年龄等无关属性改变结论）</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <p
              className={
                fairnessPercent >= 99.999
                  ? 'text-3xl font-semibold tabular-nums text-green-700'
                  : fairnessPercent >= 95
                    ? 'text-3xl font-semibold tabular-nums text-amber-600'
                    : 'text-3xl font-semibold tabular-nums text-red-600'
              }
            >
              {formatNumber(fairnessPercent, 1)}%
            </p>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-green-500"
                style={{ width: `${Math.min(100, Math.max(0, fairnessPercent))}%` }}
              />
            </div>
            <p className="text-xs text-slate-500">
              {fairnessPercent >= 99.999
                ? '全部检测用例通过'
                : '存在未通过用例，建议检查提示词与知识库'}
            </p>
          </CardContent>
        </Card>
      </section>

      {/* 步骤明细 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-slate-400" aria-hidden />
            各步骤调用明细
          </CardTitle>
        </CardHeader>
        <CardContent>
          {metrics.step_breakdown.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50/60 px-4 py-6 text-center text-sm text-slate-500">
              还没有步骤级统计数据，跑完一份诊断后再来看看。
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>步骤</TableHead>
                  <TableHead className="w-24 text-right">调用次数</TableHead>
                  <TableHead className="w-32 text-right">平均耗时</TableHead>
                  <TableHead className="w-28 text-right">成本</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {metrics.step_breakdown.map((row) => (
                  <TableRow key={row.step_name}>
                    <TableCell>
                      <span className="font-mono text-xs text-slate-400">{row.step_name}</span>
                      <span className="ml-2 text-slate-800">
                        {STEP_LABELS[row.step_name as keyof typeof STEP_LABELS] ?? ''}
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono text-slate-700">
                      {formatNumber(row.calls)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-slate-700">
                      {formatLatency(row.avg_latency_ms)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-slate-700">
                      ¥ {formatNumber(row.cost, 3)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
