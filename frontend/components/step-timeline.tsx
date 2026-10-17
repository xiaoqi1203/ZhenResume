import { Check, CircleDashed, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { StepName, StepStatus, TaskStep } from '@/lib/types';

/** 8 个 Agent 步骤的固定顺序与中文名。 */
export const STEP_ORDER: StepName[] = [
  'parse',
  'jd_match',
  'ats',
  'impact',
  'risk',
  'suggestion',
  'report',
  'qc',
];

export const STEP_LABELS: Record<StepName, string> = {
  parse: '简历解析',
  jd_match: 'JD 匹配',
  ats: 'ATS 检测',
  impact: '影响力评估',
  risk: '风险扫描',
  suggestion: '改写建议',
  report: '报告生成',
  qc: '质量校验',
};

function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined) return '—';
  return tokens.toLocaleString('zh-CN');
}

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === 'done') {
    return (
      <span className="flex h-6 w-6 items-center justify-center bg-green-600 text-white">
        <Check className="h-3.5 w-3.5" aria-hidden />
      </span>
    );
  }
  if (status === 'running') {
    return (
      <span className="flex h-6 w-6 items-center justify-center bg-brand-500 text-white">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="flex h-6 w-6 items-center justify-center bg-red-600 text-white">
        <X className="h-3.5 w-3.5" aria-hidden />
      </span>
    );
  }
  return (
    <span className="flex h-6 w-6 items-center justify-center border border-ink-300 bg-white text-ink-600">
      <CircleDashed className="h-3.5 w-3.5" aria-hidden />
    </span>
  );
}

const STATUS_TEXT: Record<StepStatus, string> = {
  pending: '等待中',
  running: '进行中',
  done: '已完成',
  failed: '失败',
};

export interface StepTimelineProps {
  /** 后端返回的步骤数组；缺失的步骤自动以 pending 补齐 */
  steps: TaskStep[];
  className?: string;
}

export function StepTimeline({ steps, className }: StepTimelineProps) {
  const byName = new Map<StepName, TaskStep>();
  for (const step of steps ?? []) {
    byName.set(step.step_name, step);
  }

  const ordered: TaskStep[] = STEP_ORDER.map(
    (name) =>
      byName.get(name) ?? {
        step_name: name,
        status: 'pending' as StepStatus,
        latency_ms: null,
        tokens: null,
        cost: null,
      },
  );

  return (
    <ol className={cn('relative space-y-1', className)}>
      {ordered.map((step, index) => {
        const isLast = index === ordered.length - 1;
        const label = STEP_LABELS[step.step_name] ?? step.step_name;
        return (
          <li key={step.step_name} className="relative flex gap-3 pb-3 last:pb-0">
            {!isLast ? (
              <span
                className={cn(
                  'absolute left-3 top-7 h-[calc(100%-1.5rem)] w-px',
                  step.status === 'done' ? 'bg-ink-300' : 'bg-ink-200',
                )}
                aria-hidden
              />
            ) : null}
            <StatusIcon status={step.status} />
            <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-3 gap-y-1 pt-0.5">
              <div className="flex min-w-0 items-center gap-2">
                <span className="tnum text-[11px] font-bold text-ink-300">{String(index + 1).padStart(2, '0')}</span>
                <span
                  className={cn(
                    'truncate text-[13px]',
                    step.status === 'pending' ? 'text-ink-600' : 'font-bold text-ink-950',
                  )}
                >
                  {label}
                </span>
                <span
                  className={cn(
                    'shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.08em]',
                    step.status === 'running' && 'text-brand-600',
                    step.status === 'done' && 'text-green-600',
                    step.status === 'failed' && 'text-red-600',
                    step.status === 'pending' && 'text-slate-400',
                  )}
                >
                  {STATUS_TEXT[step.status]}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[11px] text-ink-600">
                <span>耗时 {formatLatency(step.latency_ms)}</span>
                <span>token {formatTokens(step.tokens)}</span>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default StepTimeline;