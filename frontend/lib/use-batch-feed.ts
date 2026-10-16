'use client';

/**
 * HR 批次进度订阅：**默认走 SSE（契约 §6.5），失败自动回落到 2s 轮询**。
 *
 * 与 `useTaskFeed` 同构（同样的失败回落策略），差别是它维护的是"候选人列表"：
 * `item_done` 到达时把那一行并进 items —— 这正是价值所在，
 * HR 能看到进度条**逐个候选人点亮**，而不是 `3/100` 这种数字跳变。
 *
 * 回落触发条件（任一）：建流失败 / 流被提前掐断没收到 done / 超过服务端 3600s 上限。
 */

import * as React from 'react';

import { ApiError, getBatch, streamBatch } from '@/lib/api';
import type { BatchStatusResponse } from '@/lib/types';

export type BatchFeedMode = 'off' | 'connecting' | 'stream' | 'polling';

const POLL_INTERVAL_MS = 2000;

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

/** 把 `item_done` 的单行结果并进 items（按 resume_id + report_id 定位，避免重复行）。 */
function mergeItem(
  prev: BatchStatusResponse | null,
  item: Record<string, unknown>,
): BatchStatusResponse | null {
  if (!prev) return prev;
  const items = [...((prev.items ?? []) as unknown as Array<Record<string, unknown>>)];
  const key = (row: Record<string, unknown>) => `${row.resume_id}|${row.report_id ?? ''}`;
  const target = key(item);
  const index = items.findIndex((row) => key(row) === target || row.resume_id === item.resume_id);
  if (index >= 0) items[index] = { ...items[index], ...item };
  else items.push(item);
  // 与服务端一致的排序：有分的在前按分降序，未完成的垫底
  items.sort((a, b) => Number(a.overall_score == null) - Number(b.overall_score == null)
    || Number(b.overall_score ?? 0) - Number(a.overall_score ?? 0));
  return { ...prev, items } as unknown as BatchStatusResponse;
}

/**
 * @param batchId  当前批次的 id；为空（还没创建批次）时不做任何事
 * @param seed     批次初始快照（创建批次接口的返回值），用于作为合入的基底
 * @param onUpdate 每次有变化时回调（页面用它 setBatch）
 * @param onError  出错回调（页面用它 setBatchError）
 */
export function useBatchFeed(
  batchId: string | undefined,
  seed: BatchStatusResponse | null,
  onUpdate: (next: BatchStatusResponse) => void,
  onError: (message: string) => void,
): { mode: BatchFeedMode } {
  const [mode, setMode] = React.useState<BatchFeedMode>('off');

  const updateRef = React.useRef(onUpdate);
  const errorRef = React.useRef(onError);
  updateRef.current = onUpdate;
  errorRef.current = onError;

  // 只依赖 batchId：onUpdate 会 setBatch，若把它放进依赖会形成"更新→重建流"的死循环。
  // seed 只在 batchId 变化时读取一次作为基底，后续以流里的数据为准。
  const seedRef = React.useRef(seed);
  seedRef.current = seed;

  React.useEffect(() => {
    if (!batchId) {
      setMode('off');
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let abortStream: (() => void) | null = null;
    let finished = false;
    let current = seedRef.current;

    const stopTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const apply = (next: BatchStatusResponse | null) => {
      if (!next || cancelled) return;
      current = next;
      updateRef.current(next);
      if (next.status === 'done' || next.status === 'failed') finished = true;
    };

    /** 回落到 §6.5 的 2s 轮询（与改造前的行为一致） */
    const startPolling = () => {
      if (cancelled || finished) return;
      setMode('polling');
      stopTimer();
      const poll = async () => {
        try {
          const next = await getBatch(batchId);
          if (cancelled) return;
          apply(next);
        } catch (err) {
          if (!cancelled) errorRef.current(describeError(err));
        }
        if (!cancelled && !finished) timer = setTimeout(poll, POLL_INTERVAL_MS);
      };
      void poll();
    };

    setMode('connecting');
    abortStream = streamBatch(batchId, {
      onEvent: (event, data) => {
        if (cancelled) return;
        setMode('stream');
        if (event === 'item_done') {
          apply(mergeItem(current, data));
          return;
        }
        if (event === 'progress') {
          if (!current) return;
          apply({ ...current, ...data } as unknown as BatchStatusResponse);
          return;
        }
        if (event === 'done') {
          apply(data as unknown as BatchStatusResponse);
          return;
        }
        if (event === 'error') {
          const message = String(data?.message ?? '批次执行失败');
          // 超过流上限属于"通道不可用"，按契约回落轮询；真正的失败才报错
          if (message.includes('上限')) {
            startPolling();
            return;
          }
          finished = true;
          errorRef.current(message);
        }
      },
      onError: () => startPolling(),
      onClose: () => {
        if (!finished) startPolling();
      },
    });

    return () => {
      cancelled = true;
      stopTimer();
      abortStream?.();
    };
  }, [batchId]);

  return { mode };
}
