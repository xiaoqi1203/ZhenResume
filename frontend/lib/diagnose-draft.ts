/**
 * 诊断表单草稿：登录跳转期间保住用户已填的内容
 * ------------------------------------------------------------------
 * 背景：未登录点「开始诊断」会被送去登录页，登录成功再回到首页。
 * 如果只靠组件内存状态，跳转会整页刷新，已填的简历、JD、标题全丢。
 *
 * 设计取舍
 * - **文本字段**（JD 原文、岗位名、公司名、模式、当前幻灯片）存 localStorage，小且安全。
 * - **简历文件**（File 对象）不能序列化进 localStorage，改存 IndexedDB 的 Blob：
 *   `file.arrayBuffer()` → 存 `{ name, type, lastModified, buffer }` → 读回后重建 File。
 *   IndexedDB 容量远大于 localStorage（通常几百 MB 起），10MB 的简历毫无压力。
 * - 草稿带过期时间（默认 30 分钟），避免"上周填的东西"突然冒出来。
 *
 * 失败不影响主流程：任何一步抛错都静默降级为「不恢复草稿」，登录照常进行。
 */

const DRAFT_KEY = 'diagnose.draft.v1';
const DB_NAME = 'zhenresume-draft';
const STORE = 'files';
const TTL_MS = 30 * 60 * 1000;

export interface DiagnoseDraftText {
  jdText: string;
  jdTitle: string;
  company: string;
  mode: 'fixed' | 'agentic';
  /** 卡片当前在第几屏（0 = 首页概述，1 = 上传表单） */
  slide: number;
  /** 简历文件在 IndexedDB 里的 key（没有文件时为 null） */
  fileId: string | null;
  savedAt: number;
}

interface DraftFileRecord {
  id: string;
  name: string;
  type: string;
  lastModified: number;
  buffer: ArrayBuffer;
}

function isBrowser(): boolean {
  return typeof window !== 'undefined';
}

/* ------------------------------------------------------------------ */
/* 文本草稿                                                            */
/* ------------------------------------------------------------------ */

export function saveDraftText(draft: Omit<DiagnoseDraftText, 'savedAt'>): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...draft, savedAt: Date.now() }));
  } catch {
    /* 隐私模式 / 配额满：忽略 */
  }
}

export function loadDraftText(): DiagnoseDraftText | null {
  if (!isBrowser()) return null;
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DiagnoseDraftText;
    if (!parsed || typeof parsed !== 'object') return null;
    if (!parsed.savedAt || Date.now() - parsed.savedAt > TTL_MS) {
      window.localStorage.removeItem(DRAFT_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* 文件（IndexedDB）                                                   */
/* ------------------------------------------------------------------ */

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = window.indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** 保存简历文件；失败返回 null（不阻断登录流程） */
export async function saveDraftFile(file: File): Promise<string | null> {
  if (!isBrowser() || typeof window.indexedDB === 'undefined') return null;
  try {
    const db = await openDb();
    const id = `f-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const record: DraftFileRecord = {
      id,
      name: file.name,
      type: file.type,
      lastModified: file.lastModified,
      buffer: await file.arrayBuffer(),
    };
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(record);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
    return id;
  } catch {
    return null;
  }
}

/** 按 id 读回简历文件；不存在或失败返回 null */
export async function loadDraftFile(id: string): Promise<File | null> {
  if (!isBrowser() || typeof window.indexedDB === 'undefined') return null;
  try {
    const db = await openDb();
    const record = await new Promise<DraftFileRecord | undefined>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(id);
      req.onsuccess = () => resolve(req.result as DraftFileRecord | undefined);
      req.onerror = () => reject(req.error);
    });
    db.close();
    if (!record) return null;
    return new File([record.buffer], record.name, {
      type: record.type || 'application/octet-stream',
      lastModified: record.lastModified,
    });
  } catch {
    return null;
  }
}

export function deleteDraftFile(id: string): void {
  if (!isBrowser() || typeof window.indexedDB === 'undefined') return;
  try {
    void openDb()
      .then((db) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(id);
        tx.oncomplete = () => db.close();
      })
      .catch(() => {});
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ */
/* 组合操作                                                            */
/* ------------------------------------------------------------------ */

/** 登录跳转前保存整份草稿 */
export async function persistDiagnoseDraft(input: {
  file: File | null;
  jdText: string;
  jdTitle: string;
  company: string;
  mode: 'fixed' | 'agentic';
  slide: number;
}): Promise<void> {
  const fileId = input.file ? await saveDraftFile(input.file) : null;
  saveDraftText({
    jdText: input.jdText,
    jdTitle: input.jdTitle,
    company: input.company,
    mode: input.mode,
    slide: input.slide,
    fileId,
  });
}

export interface RestoredDraft {
  jdText: string;
  jdTitle: string;
  company: string;
  mode: 'fixed' | 'agentic';
  slide: number;
  file: File | null;
}

/** 登录返回后读取草稿（读完即删，避免下次误恢复） */
export async function consumeDiagnoseDraft(): Promise<RestoredDraft | null> {
  const draft = loadDraftText();
  if (!draft) return null;
  const file = draft.fileId ? await loadDraftFile(draft.fileId) : null;
  if (draft.fileId) deleteDraftFile(draft.fileId);
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    /* ignore */
  }
  return {
    jdText: draft.jdText ?? '',
    jdTitle: draft.jdTitle ?? '',
    company: draft.company ?? '',
    mode: draft.mode === 'agentic' ? 'agentic' : 'fixed',
    slide: Number.isFinite(draft.slide) ? draft.slide : 1,
    file,
  };
}

export function hasDraft(): boolean {
  return loadDraftText() !== null;
}
