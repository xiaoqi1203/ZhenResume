/**
 * 简历编辑器本机草稿（localStorage）。
 *
 * 为什么单独抽一个模块：
 * 1. 「开始页」（/editor）要读草稿来判断是否存在未完成的内容、并给出「继续上次草稿」入口；
 * 2. 「编辑页」（/editor/write）要恢复草稿、自动保存草稿、放弃草稿。
 * 两边共用同一个 key 与同一套归一化逻辑，放在组件里会导致两处格式漂移，
 * 严重时一边写进去、另一边读不出来（用户看到的是"草稿丢了"）。
 *
 * 设计约束：
 * - 读写都可能因为隐私模式 / localStorage 配额失败，这里全部吞掉异常：
 *   草稿是便利功能，不能让它把编辑流程弄崩；
 * - 读出来的内容必须经过 `normalizeEditorContent`，避免旧版本 / 脏数据缺字段。
 */

import { normalizeEditorContent } from '@/lib/resume-content';
import type { EditorContent } from '@/lib/types';

export const EDITOR_DRAFT_KEY = 'zhenresume.editor.draft';

export interface EditorDraft {
  title: string;
  templateCode: string | null;
  content: EditorContent;
  /**
   * 上次保存/载入到的服务端简历 ID。
   * 有值时「保存」走 PUT 更新同一份简历，避免每改一次就新建一份。
   */
  savedResumeId?: string | null;
  /** ISO 时间戳，用于在开始页显示「草稿保存于 …」 */
  savedAt?: string;
}

/** 读取本机草稿；没有草稿或数据不可用时返回 null。 */
export function readEditorDraft(): EditorDraft | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(EDITOR_DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<EditorDraft>;
    if (!parsed || typeof parsed !== 'object' || !parsed.content) return null;
    return {
      title: typeof parsed.title === 'string' ? parsed.title : '',
      templateCode: typeof parsed.templateCode === 'string' ? parsed.templateCode : null,
      content: normalizeEditorContent(parsed.content),
      savedResumeId:
        typeof parsed.savedResumeId === 'string' && parsed.savedResumeId
          ? parsed.savedResumeId
          : null,
      savedAt: typeof parsed.savedAt === 'string' ? parsed.savedAt : undefined,
    };
  } catch {
    // 隐私模式 / 脏数据：直接当作没有草稿
    return null;
  }
}

/** 写入草稿；成功时返回本次保存的 ISO 时间戳，失败返回 null（不影响编辑）。 */
export function saveEditorDraft(draft: EditorDraft): string | null {
  if (typeof window === 'undefined') return null;
  const savedAt = new Date().toISOString();
  try {
    window.localStorage.setItem(EDITOR_DRAFT_KEY, JSON.stringify({ ...draft, savedAt }));
    return savedAt;
  } catch {
    // 隐私模式 / 配额不足：忽略，不影响编辑
    return null;
  }
}

/** 删除本机草稿。 */
export function clearEditorDraft(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(EDITOR_DRAFT_KEY);
  } catch {
    // 忽略：隐私模式下无法写入
  }
}