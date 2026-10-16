'use client';

/**
 * 任务进度订阅：**默认走 SSE（契约 §4.4），任何失败自动回落到轮询（§4.2）**。
 *
 * 与样式完全无关 —— 只负责把数据交给页面。换前端风格时不需要动这个文件。
 *
 * 回落触发条件（任一）：
 *   1. 建流失败（非 2xx / 网络错误 / 代理不支持 SSE）；
 *   2. 流被对端提前关闭但没收到 `done`（比如网关超时掐断）；
 *   3. 服务端因超过 900s 上限主动发 `error`（契约规定此时客户端应回落轮询）。
 * 也就是说：**SSE 只负责"更快"，可用性仍然由轮询兜底**，不会出现白屏。
 */

import * as React from 'react';

import { ApiError, getTask, streamTask } from '@/lib/api';
import type { TaskStatusResponse } from '@/lib/types';

export type TaskFeedMode = 'connecting' | 'stream' | 'polling';

const POLL_INTERVAL_MS = 1500;

export interface TaskFeed {
  task: TaskStatusResponse | null;
  error: string | null;
  loading: boolean;
  /** 当前实际使用的通道，便于界面提示"实时推送 / 已回落到轮询" */
  mode: TaskFeedMode;
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

/** 把 `step_done` 的单步结果并进 steps（首个事件可能早于任何完整快照）。 */
function mergeStep(
  prev: TaskStatusResponse | null,
  taskId: string,
  step: Record<string, unknown>,
): TaskStatusResponse {
  const base: TaskStatusResponse =
    prev ??
    ({
      task_id: taskId,
      status: 'running',
      progress: 0,
      current_step: null,
      total_steps: 8,
      steps: [],
      report_id: null,
      error: null,
    } as unknown as TaskStatusResponse);

  const steps = [
    ...((base.steps ?? []) as unknown as Array<Record<string, unknown>>),
  ];
  const index = steps.findIndex((s) => s.step_name === step.step_name);
  if (index >= 0) steps[index] = { ...steps[index], ...step };
  else steps.push(step);

  return { ...base, steps } as unknown as TaskStatusResponse;
}

export function useTaskFeed(
  taskId: string,
  retryToken: number,
  onDone?: (task: TaskStatusResponse) => void,
): TaskFeed {
  const [task, setTask] = React.useState<TaskStatusResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [mode, setMode] = React.useState<TaskFeedMode>('connecting');

  const doneRef = React.useRef(onDone);
  doneRef.current = onDone;

  React.useEffect(() => {
    if (!taskId) {
      setLoading(false);
      setError('缺少任务 ID，请返回首页重新发起诊断。');
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let abortStream: (() => void) | null = null;
    let finished = false;

    const stopTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const handleDone = (full: TaskStatusResponse) => {
      if (cancelled || finished) return;
      finished = true;
      setTask(full);
      setError(full.error ?? null);
      setLoading(false);
      doneRef.current?.(full);
    };

    /** 回落到 §4.2 轮询（与改造前的行为完全一致） */
    const startPolling = () => {
      if (cancelled || finished) return;
      setMode('polling');
      stopTimer();
      const poll = async () => {
        try {
          const data = await getTask(taskId);
          if (cancelled) return;
          setTask(data);
          setLoading(false);
          setError(data.error ?? null);
          if (data.status === 'done' && data.report_id) {
            handleDone(data);
            return;
          }
          if (data.status === 'failed') return;
        } catch (err) {
          if (cancelled) return;
          setLoading(false);
          setError(describeError(err));
        }
        if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL_MS);
      };
      void poll();
    };

    setMode('connecting');
    abortStream = streamTask(taskId, {
      onEvent: (event, data) => {
        if (cancelled) return;
        setMode('stream'); // 幂等：React 对相同值不会重渲染
        setLoading(false);

        if (event === 'done') {
          handleDone(data as unknown as TaskStatusResponse);
          return;
        }
        if (event === 'error') {
          const message = String(data?.message ?? '任务失败');
          // 超时上限属于"流不可用"，按契约回落轮询；真正的任务失败则直接报错
          if (message.includes('上限')) {
            startPolling();
            return;
          }
          finished = true;
          setError(message);
          return;
        }
        if (event === 'step_done') {
          setTask((prev) => mergeStep(prev, taskId, data));
          return;
        }
        if (event === 'progress') {
          setTask((prev) => ({ ...(prev ?? ({} as TaskStatusResponse)), ...data }) as TaskStatusResponse);
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
  }, [taskId, retryToken]);

  return { task, error, loading, mode };
}
