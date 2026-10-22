'use client';

/**
 * 「上次诊断」本地缓存。
 *
 * 解决的问题：诊断完成后页面会跳到报告页；用户一旦回到首页或刷新，
 * 这份报告就"找不回来了"（后端有数据，但前端没有任何入口）。
 *
 * 这里只存**指针**（taskId / reportId / 时间 / 岗位名），不存报告正文；
 * 报告本身仍在服务端，按 device-id 归属校验，换设备/清缓存都拿不到别人的数据。
 */

const KEY = 'zr:last-run';

export interface LastRun {
  taskId: string;
  reportId: string | null;
  /** 岗位名（JD 标题），用于入口文案 */
  title: string;
  /** 模式：fixed / agentic */
  mode: 'fixed' | 'agentic';
  /** ISO 时间戳 */
  at: string;
}

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

export function readLastRun(): LastRun | null {
  if (!canUseStorage()) return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastRun>;
    if (!parsed || typeof parsed.taskId !== 'string' || !parsed.taskId) return null;
    return {
      taskId: parsed.taskId,
      reportId: typeof parsed.reportId === 'string' ? parsed.reportId : null,
      title: typeof parsed.title === 'string' ? parsed.title : '',
      mode: parsed.mode === 'agentic' ? 'agentic' : 'fixed',
      at: typeof parsed.at === 'string' ? parsed.at : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function saveLastRun(patch: Partial<LastRun> & { taskId: string }): LastRun {
  const prev = readLastRun();
  const next: LastRun = {
    taskId: patch.taskId,
    reportId: patch.reportId ?? prev?.reportId ?? null,
    title: patch.title ?? prev?.title ?? '',
    mode: patch.mode ?? prev?.mode ?? 'fixed',
    at: patch.at ?? new Date().toISOString(),
  };
  if (canUseStorage()) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* 隐私模式/配额满：缓存失败不影响主流程 */
    }
  }
  return next;
}

export function clearLastRun(): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
