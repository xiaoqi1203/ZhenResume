'use client';

import * as React from 'react';
import { ArrowRight, Coins, Gauge, Timer } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TaskUsage } from '@/lib/types';

/** 毫秒 → 「12.4 秒」/「1 分 05 秒」。
 *
 * 口径：
 * - 不足 1 分钟：保留 1 位小数（调用方需按 <1s 的间隔刷新，否则小数会一直停在 .0）；
 * - 达到 1 分钟：只显示「x 分 xx 秒」，不再显示 0.x。
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return '—';
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes} 分 ${String(rest).padStart(2, '0')} 秒`;
}

/** 人民币，保留到分；极小额（<0.01）保留 4 位，避免显示成 ¥0.00。 */
export function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined || !Number.isFinite(cost)) return '—';
  if (cost > 0 && cost < 0.01) return `¥${cost.toFixed(4)}`;
  return `¥${cost.toFixed(2)}`;
}

export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens)) return '—';
  return tokens.toLocaleString('zh-CN');
}

export interface UsageSummaryProps {
  usage: TaskUsage;
  className?: string;
}

/**
 * 诊断用量三联：耗时 / Token / 花费。
 *
 * 为什么单独抽组件：分析页（完成后停留）与报告页都要展示同一组数字，
 * 两处各写一遍必然出现"一边四舍五入一边不四舍五入"这类不一致。
 */
export function UsageSummary({ usage, className }: UsageSummaryProps) {
  const hasEvidence = (usage.evidence_cost ?? 0) > 0 || (usage.evidence_tokens ?? 0) > 0;

  return (
    <div className={cn('grid grid-cols-3 gap-px bg-ink-200', className)}>
      <div className="bg-white px-4 py-4">
        <p className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
          <Timer className="h-3 w-3" aria-hidden />
          总耗时
        </p>
        <p className="mt-1.5 tnum text-[22px] font-extrabold leading-none tracking-tight text-ink-950">
          {formatDuration(usage.latency_ms)}
        </p>
      </div>
      <div className="bg-white px-4 py-4">
        <p className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
          <Gauge className="h-3 w-3" aria-hidden />
          Token
        </p>
        <p className="mt-1.5 tnum text-[22px] font-extrabold leading-none tracking-tight text-ink-950">
          {formatTokens(usage.tokens)}
        </p>
      </div>
      <div className="bg-white px-4 py-4">
        <p className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
          <Coins className="h-3 w-3" aria-hidden />
          花费
        </p>
        <p className="mt-1.5 tnum text-[22px] font-extrabold leading-none tracking-tight text-ink-950">
          {formatCost(usage.cost)}
        </p>
      </div>
      {hasEvidence ? (
        <p className="col-span-3 bg-white px-4 pb-3 text-[11px] leading-relaxed text-ink-500">
          其中知识库证据检索 {formatDuration(usage.evidence_latency_ms)}、
          {formatTokens(usage.evidence_tokens)} token、{formatCost(usage.evidence_cost)}
          {usage.evidence_cost > 0 && usage.steps_cost > 0 && usage.evidence_cost > usage.steps_cost
            ? ' —— 这部分主要用于给每条结论挂上可核对的知识库依据，是"证据优先"的代价。'
            : '。'}
        </p>
      ) : null}
    </div>
  );
}

export interface CompletionHoldProps {
  usage: TaskUsage | null | undefined;
  /** 剩余秒数（由父组件倒计时，便于"立即查看"时清零） */
  remainingSeconds: number;
  /** 总停留秒数（画进度条用） */
  totalSeconds: number;
  paused: boolean;
  onGoNow: () => void;
  onTogglePause: () => void;
  className?: string;
}

/**
 * 诊断完成后的"停留页"。
 *
 * 解决的实际问题（用户原话：「分析结束时可以停留一下，让用户能看到耗时和花费 Token」）：
 * 原来 `status=done` 立刻 `router.replace` 跳报告页，这一页最关键的三个数字
 * ——花了多少时间、用了多少 token、花了多少钱 —— 用户一眼都没看到就消失了。
 *
 * 设计取舍：
 * - **默认自动跳转但可暂停/立即走**：不强制用户点一下才能继续（那是把"展示"变成"阻塞"），
 *   也不无限停留（用户可能只是回来刷新页面）；
 * - 倒计时用 `aria-live="polite"` 播报，无障碍下也知道即将跳转；
 * - 停留时长只在**首次**完成时给足（从报告页返回不重复倒计时）。
 */
export function CompletionHold({
  usage,
  remainingSeconds,
  totalSeconds,
  paused,
  onGoNow,
  onTogglePause,
  className,
}: CompletionHoldProps) {
  const ratio = totalSeconds > 0 ? Math.max(0, Math.min(1, remainingSeconds / totalSeconds)) : 0;

  return (
    <div className={cn('border border-ink-950 bg-white', className)}>
      <div className="border-b border-ink-200 bg-ink-50 px-5 py-4">
        <p className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-green-700">
          诊断完成
        </p>
        <p className="mt-1.5 text-[17px] font-extrabold leading-snug tracking-tight text-ink-950">
          这次诊断的消耗
        </p>
      </div>

      {usage ? (
        <UsageSummary usage={usage} />
      ) : (
        <div className="px-5 py-4 text-[12px] text-ink-500">用量数据未返回。</div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-200 px-5 py-3">
        <p className="text-[11.5px] text-ink-600" aria-live="polite">
          {paused ? (
            <>已暂停自动跳转。</>
          ) : (
            <>
              <span className="tnum font-bold text-ink-950">{remainingSeconds}</span> 秒后自动打开完整报告
            </>
          )}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onTogglePause}
            className="border border-ink-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-ink-700 transition-colors hover:border-ink-950 hover:text-ink-950"
          >
            {paused ? '继续倒计时' : '先别跳'}
          </button>
          <button
            type="button"
            onClick={onGoNow}
            className="inline-flex items-center gap-1 border border-ink-950 bg-ink-950 px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-white hover:text-ink-950"
          >
            立即查看报告
            <ArrowRight className="h-3 w-3" aria-hidden />
          </button>
        </div>
      </div>

      <div className="h-1 w-full bg-ink-200">
        <div
          className="h-full bg-green-600 transition-[width] duration-1000 ease-linear"
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
    </div>
  );
}

export default CompletionHold;
