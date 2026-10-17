'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ClipboardCopy,
  Download,
  FileText,
  FileUp,
  FolderOpen,
  Lightbulb,
  Loader2,
  Plus,
  Printer,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import {
  ApiError,
  createJd,
  createResumeFromEditor,
  createTask,
  downloadResumeExport,
  getResume,
  getTemplate,
  listResumes,
  listTemplates,
  uploadResume,
} from '@/lib/api';
import type {
  EditorContent,
  EditorEducationItem,
  EditorProjectItem,
  EditorWorkItem,
  ExportFormat,
  ResumeSummary,
  TemplateDetail,
  TemplateSection,
  TemplateSectionKey,
  TemplateSummary,
  TemplateTarget,
} from '@/lib/types';
import { createEmptyEditorContent } from '@/lib/types';
import {
  buildResumeMarkdown,
  countFilledSections,
  defaultResumeTitle,
  editorContentFromParsed,
  emptyKeySections,
  isSectionFilled,
  normalizeEditorContent,
  SECTION_TITLES,
} from '@/lib/resume-content';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ACCEPT_ATTRIBUTE, validateResumeFile } from '@/components/upload-dropzone';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { ErrorBanner } from '@/components/states';
import { ResumePreview } from '@/components/editor/resume-preview';

/* ------------------------------------------------------------------ */
/* 常量                                                                */
/* ------------------------------------------------------------------ */

const DRAFT_KEY = 'zhenresume.editor.draft';
const JD_MIN_LENGTH = 20;

const PDF_UNAVAILABLE_HINT =
  '服务端未配置 PDF 转换组件，请改用 DOCX，或按 Ctrl+P 用浏览器打印为 PDF';

const TARGET_LABELS: Record<TemplateTarget, string> = {
  campus: '应届',
  experienced: '社招',
  transition: '转行',
};

/** 表单区块顺序（默认展开前 4 个）。 */
const BLOCK_KEYS: readonly TemplateSectionKey[] = [
  'basics',
  'summary',
  'work',
  'projects',
  'education',
  'skills',
  'certificates',
  'awards',
  'custom',
];

const DEFAULT_OPEN_KEYS: readonly TemplateSectionKey[] = ['basics', 'summary', 'work', 'projects'];

type Phase = 'idle' | 'saving_resume' | 'parsing_jd' | 'creating_task' | 'redirecting';

const PHASE_TEXT: Record<Exclude<Phase, 'idle'>, string> = {
  saving_resume: '正在保存简历…',
  parsing_jd: '正在解析岗位 JD…',
  creating_task: '正在创建诊断任务…',
  redirecting: '任务已创建，正在进入诊断进度页…',
};

type Status = 'idle' | 'loading' | 'ready' | 'error';

interface EditorDraft {
  title: string;
  templateCode: string | null;
  content: EditorContent;
  savedAt?: string;
}

/* ------------------------------------------------------------------ */
/* 工具函数                                                            */
/* ------------------------------------------------------------------ */

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

function snapshotKey(title: string, templateCode: string | null, content: EditorContent): string {
  return JSON.stringify([title, templateCode, content]);
}

function triggerDownload(url: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  if (url.startsWith('blob:')) {
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
}

function parseFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''));
    } catch {
      return null;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ? plain[1].trim() : null;
}

function readDraft(): EditorDraft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<EditorDraft>;
    if (!parsed || typeof parsed !== 'object' || !parsed.content) return null;
    return {
      title: typeof parsed.title === 'string' ? parsed.title : '',
      templateCode: typeof parsed.templateCode === 'string' ? parsed.templateCode : null,
      content: normalizeEditorContent(parsed.content),
      savedAt: typeof parsed.savedAt === 'string' ? parsed.savedAt : undefined,
    };
  } catch {
    // 隐私模式 / 脏数据：直接当作没有草稿
    return null;
  }
}

function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // 忽略：隐私模式下无法写入
  }
}

/* ------------------------------------------------------------------ */
/* 小部件                                                              */
/* ------------------------------------------------------------------ */

function Field({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn('block space-y-1', className)}>
      <span className="text-xs font-medium text-slate-600">{label}</span>
      {children}
    </label>
  );
}

function StringListEditor({
  items,
  onChange,
  placeholder,
  addLabel,
  itemLabel,
  multiline = false,
  rows = 2,
}: {
  items: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  addLabel: string;
  itemLabel: string;
  multiline?: boolean;
  rows?: number;
}) {
  const update = (index: number, value: string) => {
    onChange(items.map((item, i) => (i === index ? value : item)));
  };
  const remove = (index: number) => {
    if (!window.confirm(`确定删掉${itemLabel}吗？`)) return;
    onChange(items.filter((_, i) => i !== index));
  };
  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    onChange(next);
  };

  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <div key={index} className="flex items-start gap-2">
          {multiline ? (
            <Textarea
              value={item}
              rows={rows}
              placeholder={placeholder}
              onChange={(event) => update(index, event.target.value)}
              className="min-h-[64px]"
            />
          ) : (
            <Input
              value={item}
              placeholder={placeholder}
              onChange={(event) => update(index, event.target.value)}
            />
          )}
          <div className="flex shrink-0 items-center gap-1 pt-1">
            <Button
              size="icon"
              variant="ghost"
              aria-label="上移"
              title="上移"
              disabled={index === 0}
              onClick={() => move(index, -1)}
            >
              <ArrowUp className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label="下移"
              title="下移"
              disabled={index === items.length - 1}
              onClick={() => move(index, 1)}
            >
              <ArrowDown className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label="删除"
              title="删掉这条"
              className="text-slate-400 hover:text-red-600"
              onClick={() => remove(index)}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        </div>
      ))}
      <Button size="sm" variant="outline" onClick={() => onChange([...items, ''])}>
        <Plus className="h-3.5 w-3.5" aria-hidden />
        {addLabel}
      </Button>
    </div>
  );
}

function TagEditor({
  tags,
  onChange,
  placeholder,
  addLabel,
}: {
  tags: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  addLabel: string;
}) {
  const [draft, setDraft] = React.useState('');

  const commit = (raw: string) => {
    const parts = raw
      .split(/[,，、;；/|]+/)
      .map((part) => part.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    onChange(Array.from(new Set([...tags, ...parts])));
    setDraft('');
  };

  return (
    <div className="space-y-2">
      {tags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag, index) => (
            <span
              key={`${tag}-${index}`}
              className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700"
            >
              {tag}
              <button
                type="button"
                aria-label={`删除 ${tag}`}
                className="text-slate-400 transition-colors hover:text-red-600"
                onClick={() => onChange(tags.filter((_, i) => i !== index))}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="text-xs text-slate-400">还没有添加内容。</p>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              commit(draft);
            }
          }}
          onBlur={() => {
            if (/[,，、;；/|]/.test(draft)) commit(draft);
          }}
          className="h-9"
        />
        <Button size="sm" variant="outline" className="h-9" onClick={() => commit(draft)}>
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {addLabel}
        </Button>
      </div>
      <p className="text-[11px] text-slate-400">
        支持用逗号或顿号一次输入多个，例如「Python, MySQL、Redis」；按 Enter 也可添加。
      </p>
    </div>
  );
}

function CollapsibleBlock({
  title,
  sectionKey,
  section,
  filled,
  open,
  onToggle,
  children,
}: {
  title: string;
  sectionKey: TemplateSectionKey;
  section?: TemplateSection;
  filled: boolean;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <Card className="print:hidden" data-section={sectionKey}>
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 p-5 text-left"
      >
        <span className="flex items-center gap-2">
          <ChevronDown
            className={cn(
              'h-4 w-4 shrink-0 text-slate-400 transition-transform',
              !open && '-rotate-90',
            )}
            aria-hidden
          />
          <span className="text-base font-semibold tracking-tight text-slate-900">{title}</span>
          {section?.required ? (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">
              建议填写
            </span>
          ) : null}
        </span>
        <span className="flex items-center gap-2 text-xs">
          {filled ? (
            <span className="inline-flex items-center gap-1 text-ink-950">
              <Check className="h-3.5 w-3.5" aria-hidden />
              已填写
            </span>
          ) : (
            <span className="text-slate-400">未填写</span>
          )}
        </span>
      </button>
      {open ? (
        <CardContent className="space-y-4">
          {section?.tip ? (
            <p className="border-l-2 border-ink-950 bg-ink-50 px-3 py-2 text-[11.5px] leading-relaxed text-ink-700">
              {section.tip}
            </p>
          ) : null}
          {children}
          {section && section.examples.length > 0 ? (
            <p className="text-[11px] leading-relaxed text-slate-400">
              示例：{section.examples.join('　/　')}
            </p>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* 主体                                                                */
/* ------------------------------------------------------------------ */

export function EditorWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [title, setTitle] = React.useState('');
  const [templateCode, setTemplateCode] = React.useState<string | null>(null);
  const [content, setContent] = React.useState<EditorContent>(() => createEmptyEditorContent());

  const [templates, setTemplates] = React.useState<TemplateSummary[]>([]);
  const [templatesStatus, setTemplatesStatus] = React.useState<Status>('loading');
  const [templatesError, setTemplatesError] = React.useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = React.useState<TemplateDetail | null>(null);
  const [templateLoading, setTemplateLoading] = React.useState(false);
  const [templateError, setTemplateError] = React.useState<string | null>(null);

  const [resumes, setResumes] = React.useState<ResumeSummary[]>([]);
  const [resumesStatus, setResumesStatus] = React.useState<Status>('idle');
  const [resumesError, setResumesError] = React.useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = React.useState(false);

  const [openBlocks, setOpenBlocks] = React.useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    BLOCK_KEYS.forEach((key) => {
      initial[key] = DEFAULT_OPEN_KEYS.includes(key);
    });
    return initial;
  });

  const [draftRestored, setDraftRestored] = React.useState(false);
  const [draftReady, setDraftReady] = React.useState(false);
  const [draftSavedAt, setDraftSavedAt] = React.useState<string | null>(null);

  const [savedResumeId, setSavedResumeId] = React.useState<string | null>(null);
  const [savedKey, setSavedKey] = React.useState<string | null>(null);

  const [exporting, setExporting] = React.useState<ExportFormat | null>(null);
  const [copyState, setCopyState] = React.useState<'idle' | 'copied' | 'failed'>('idle');
  const copyTimer = React.useRef<number | null>(null);
  const [importing, setImporting] = React.useState(false);
  const [dragActive, setDragActive] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  const [jdText, setJdText] = React.useState('');
  const [jdTitle, setJdTitle] = React.useState('');
  const [company, setCompany] = React.useState('');
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const busy = phase !== 'idle';
  const initRef = React.useRef(false);

  const currentKey = React.useMemo(
    () => snapshotKey(title, templateCode, content),
    [title, templateCode, content],
  );
  const upToDate = savedResumeId !== null && savedKey === currentKey;
  const filledCount = React.useMemo(() => countFilledSections(content), [content]);
  const missingKeySections = React.useMemo(() => emptyKeySections(content), [content]);
  const sectionMeta = React.useMemo(() => {
    const map = new Map<TemplateSectionKey, TemplateSection>();
    activeTemplate?.sections.forEach((section) => map.set(section.key, section));
    return map;
  }, [activeTemplate]);

  /* ---------------- 数据加载 ---------------- */

  const loadTemplateList = React.useCallback(async () => {
    setTemplatesStatus('loading');
    setTemplatesError(null);
    try {
      const data = await listTemplates();
      setTemplates(data.items);
      setTemplatesStatus('ready');
    } catch (err) {
      setTemplatesError(describeError(err));
      setTemplatesStatus('error');
    }
  }, []);

  const applyTemplate = React.useCallback(async (code: string) => {
    setTemplateCode(code);
    setTemplateLoading(true);
    setTemplateError(null);
    try {
      const detail = await getTemplate(code);
      setActiveTemplate(detail);
    } catch (err) {
      setActiveTemplate(null);
      setTemplateError(describeError(err));
    } finally {
      setTemplateLoading(false);
    }
  }, []);

  const loadResumeList = React.useCallback(async () => {
    setResumesStatus('loading');
    setResumesError(null);
    try {
      const data = await listResumes({ limit: 20 });
      setResumes(data.items);
      setResumesStatus('ready');
    } catch (err) {
      setResumesError(describeError(err));
      setResumesStatus('error');
    }
  }, []);

  const loadResumeById = React.useCallback(async (resumeId: string) => {
    setError(null);
    setNotice(null);
    try {
      const resume = await getResume(resumeId);
      const nextContent = resume.editor_content
        ? normalizeEditorContent(resume.editor_content)
        : editorContentFromParsed(resume.parsed);
      const nextTitle = resume.title || defaultResumeTitle(nextContent);
      setContent(nextContent);
      setTitle(nextTitle);
      setSavedResumeId(resume.resume_id);
      setSavedKey(snapshotKey(nextTitle, null, nextContent));
      setNotice(
        resume.editor_content
          ? '已载入这份简历的编辑器内容，修改后可以直接再次发起诊断。'
          : '这份简历来自上传文件，已把解析结果填入编辑器；个人优势、荣誉奖项、自定义区块需要你补充。',
      );
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  /**
   * 从本机文档导入：上传 → 解析 → 填入表单。
   * 有了这个入口，用户在编辑器里就能直接拿现有简历起步，不必先绕到首页上传一次。
   */
  const importFromFile = React.useCallback(
    async (file: File) => {
      setError(null);
      setNotice(null);

      const problem = validateResumeFile(file);
      if (problem) {
        setError(problem);
        return;
      }
      if (
        countFilledSections(content) > 0 &&
        !window.confirm('导入会覆盖当前表单里已填写的内容，继续吗？')
      ) {
        return;
      }

      setImporting(true);
      try {
        const resume = await uploadResume(file, file.name);
        const nextContent = resume.editor_content
          ? normalizeEditorContent(resume.editor_content)
          : editorContentFromParsed(resume.parsed);
        const nextTitle = resume.title || defaultResumeTitle(nextContent);

        setContent(nextContent);
        setTitle(nextTitle);
        setTemplateCode(null);

        // 刻意不沿用上传件的 ID：上传件没有编辑器原文，导出只能以解析结果为源（有损）。
        // 置空之后，下一次「导出」或「发起诊断」会另存为一份带完整结构化内容的可编辑版本。
        setSavedResumeId(null);
        setSavedKey(null);

        setNotice(
          `已从《${file.name}》导入并填入表单。请检查解析结果 —— 个人优势、荣誉奖项、自定义区块通常需要补充，确认后即可导出或发起诊断。`,
        );
        void loadResumeList();
      } catch (err) {
        setError(describeError(err));
      } finally {
        setImporting(false);
      }
    },
    [content, loadResumeList],
  );

  React.useEffect(() => {
    void loadTemplateList();
  }, [loadTemplateList]);

  /* ---------------- 初始化：草稿 + URL 参数 ---------------- */

  React.useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;

    const draft = readDraft();
    if (draft) {
      setTitle(draft.title);
      setTemplateCode(draft.templateCode);
      setContent(draft.content);
      setDraftRestored(true);
      setDraftSavedAt(draft.savedAt ?? null);
    }
    setDraftReady(true);

    const paramTemplate = searchParams.get('template');
    const paramResumeId = searchParams.get('resumeId');

    if (paramTemplate) void applyTemplate(paramTemplate);
    if (paramResumeId) void loadResumeById(paramResumeId);
  }, [applyTemplate, loadResumeById, searchParams]);

  /* ---------------- 草稿自动保存（防抖 500ms） ---------------- */

  React.useEffect(() => {
    if (!draftReady) return;
    const timer = window.setTimeout(() => {
      try {
        const payload: EditorDraft = {
          title,
          templateCode,
          content,
          savedAt: new Date().toISOString(),
        };
        window.localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
        setDraftSavedAt(payload.savedAt ?? null);
      } catch {
        // 隐私模式 / 配额不足：忽略，不影响编辑
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [content, draftReady, templateCode, title]);

  React.useEffect(() => {
    return () => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  /* ---------------- 内容编辑 ---------------- */

  const updateContent = React.useCallback((updater: (prev: EditorContent) => EditorContent) => {
    setContent((prev) => updater(prev));
  }, []);

  const updateWork = (index: number, patch: Partial<EditorWorkItem>) => {
    updateContent((prev) => ({
      ...prev,
      work: prev.work.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }));
  };

  const updateProject = (index: number, patch: Partial<EditorProjectItem>) => {
    updateContent((prev) => ({
      ...prev,
      projects: prev.projects.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }));
  };

  const updateEducation = (index: number, patch: Partial<EditorEducationItem>) => {
    updateContent((prev) => ({
      ...prev,
      education: prev.education.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }));
  };

  const moveWork = (index: number, delta: number) => {
    updateContent((prev) => {
      const target = index + delta;
      if (target < 0 || target >= prev.work.length) return prev;
      const next = [...prev.work];
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return { ...prev, work: next };
    });
  };

  const moveProject = (index: number, delta: number) => {
    updateContent((prev) => {
      const target = index + delta;
      if (target < 0 || target >= prev.projects.length) return prev;
      const next = [...prev.projects];
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return { ...prev, projects: next };
    });
  };

  const confirmRemove = (label: string): boolean => window.confirm(`确定删掉${label}吗？`);

  /* ---------------- 载入模板示例 / 已上传简历 ---------------- */

  const handleLoadExample = () => {
    if (!activeTemplate) return;
    if (!window.confirm('载入模板示例会覆盖当前已填写的内容，确定继续吗？')) return;
    const example = normalizeEditorContent(activeTemplate.example_content);
    setContent(example);
    setTitle((prev) => prev.trim() || defaultResumeTitle(example));
    setSavedResumeId(null);
    setSavedKey(null);
    setNotice(`已载入「${activeTemplate.name}」示例内容，可以直接改成你自己的经历。`);
  };

  const handleClearAll = () => {
    if (!window.confirm('清空会删除当前所有已填写内容，确定继续吗？')) return;
    setContent(createEmptyEditorContent());
    setTitle('');
    setTemplateCode(activeTemplate?.code ?? null);
    setSavedResumeId(null);
    setSavedKey(null);
    setNotice('已清空，可以从空白开始手写。');
  };

  const handleDiscardDraft = () => {
    clearDraft();
    setContent(createEmptyEditorContent());
    setTitle('');
    setTemplateCode(null);
    setSavedResumeId(null);
    setSavedKey(null);
    setDraftRestored(false);
    setDraftSavedAt(null);
    setNotice('已放弃上次的草稿。');
  };

  const handleTogglePicker = () => {
    const next = !pickerOpen;
    setPickerOpen(next);
    if (next && (resumesStatus === 'idle' || resumesStatus === 'error')) void loadResumeList();
  };

  /* ---------------- 保存 / 导出 ---------------- */

  const ensureResumeSaved = React.useCallback(async (): Promise<string> => {
    const key = snapshotKey(title, templateCode, content);
    if (savedResumeId && savedKey === key) return savedResumeId;
    const finalTitle = title.trim() || defaultResumeTitle(content);
    const resume = await createResumeFromEditor({
      title: finalTitle,
      template_code: templateCode,
      content: normalizeEditorContent(content),
    });
    setSavedResumeId(resume.resume_id);
    setSavedKey(snapshotKey(finalTitle, templateCode, content));
    if (!title.trim()) setTitle(finalTitle);
    return resume.resume_id;
  }, [content, savedKey, savedResumeId, templateCode, title]);

  const runExport = async (format: ExportFormat) => {
    if (exporting) return;
    setExporting(format);
    setError(null);
    setNotice(null);
    try {
      const resumeId = await ensureResumeSaved();

      // 三种格式统一走 fetch + blob，而不是 `<a href download>`。
      // 原因：后端按 `X-Device-Id` 校验简历归属，而浏览器的原生导航不会带这个自定义头，
      // 直接点链接会拿到 404 RESUME_NOT_FOUND。顺带还能读到状态码，
      // 从而把 503 PDF_UNAVAILABLE（服务端没配 LibreOffice）与真正的错误区分开。
      const { blob, contentDisposition } = await downloadResumeExport(resumeId, format);

      const extension = format === 'markdown' ? 'md' : format;
      const filename =
        parseFilename(contentDisposition) ||
        `${title.trim() || defaultResumeTitle(content)}.${extension}`;
      triggerDownload(URL.createObjectURL(blob), filename);

      const labels: Record<ExportFormat, string> = { markdown: 'Markdown', docx: 'DOCX', pdf: 'PDF' };
      setNotice(`已保存并开始下载 ${labels[format]} 文件。`);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'PDF_UNAVAILABLE') {
        setNotice(PDF_UNAVAILABLE_HINT);
      } else {
        setError(describeError(err));
      }
    } finally {
      setExporting(null);
    }
  };

  /* ---------------- 复制 Markdown ---------------- */

  const handleCopyMarkdown = async () => {
    try {
      const markdown = buildResumeMarkdown(content);
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(markdown);
      } else {
        throw new Error('clipboard unavailable');
      }
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopyState('idle'), 2400);
  };

  /* ---------------- 发起诊断 ---------------- */

  const handleDiagnose = async () => {
    if (busy) return;
    setError(null);
    setNotice(null);

    const jd = jdText.trim();
    if (jd.length < JD_MIN_LENGTH) {
      setError(`请粘贴目标岗位 JD，至少 ${JD_MIN_LENGTH} 个字，便于做岗位匹配分析。`);
      return;
    }

    try {
      setPhase('saving_resume');
      const resumeId = await ensureResumeSaved();

      setPhase('parsing_jd');
      const jdRecord = await createJd({
        title: jdTitle.trim() || '目标岗位',
        company: company.trim() || undefined,
        raw_text: jd,
      });

      setPhase('creating_task');
      const task = await createTask({ resume_id: resumeId, jd_id: jdRecord.jd_id });

      setPhase('redirecting');
      router.push(`/analyze/${task.task_id}`);
    } catch (err) {
      setError(describeError(err));
      setPhase('idle');
    }
  };

  /* ---------------- 渲染 ---------------- */

  return (
    <div className="space-y-6">
      {/* 顶部：标题 + 完成度 + 工具栏 */}
      <div className="space-y-4 print:hidden">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
              <Sparkles className="h-5 w-5 text-ink-950" aria-hidden />
              简历编辑器
            </h1>
            <p className="text-sm text-slate-600">
              按结构化字段填写，右侧实时预览；可以直接导出，也可以用这份简历发起一次诊断。
            </p>
          </div>
          <div className="flex flex-col items-start gap-1 sm:items-end">
            <span className="border border-ink-950 px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-ink-950">
              已填 {filledCount}/{BLOCK_KEYS.length} 个区块
            </span>
            {draftSavedAt ? (
              <span className="text-[11px] text-slate-400">草稿已自动保存在本机浏览器</span>
            ) : null}
          </div>
        </div>

        {draftRestored ? (
          <div className="flex flex-wrap items-center justify-between gap-2 border border-ink-950 bg-ink-50 px-4 py-2 text-[12px] text-ink-800">
            <span>已恢复上次的草稿，可以直接继续编辑。</span>
            <Button size="sm" variant="outline" onClick={handleDiscardDraft}>
              放弃草稿
            </Button>
          </div>
        ) : null}

        {missingKeySections.length > 0 ? (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs leading-relaxed text-amber-800">
            <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>
              关键区块还空着：{missingKeySections.join('、')}。补上能明显提升诊断结果的可用性（不填也可以提交）。
            </span>
          </div>
        ) : null}

        {error ? <ErrorBanner message={error} onRetry={() => setError(null)} /> : null}

        {notice ? (
          <div className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs leading-relaxed text-slate-600">
            <span>{notice}</span>
            <button
              type="button"
              aria-label="关闭提示"
              className="text-slate-400 hover:text-slate-600"
              onClick={() => setNotice(null)}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        ) : null}

        {/* 模板选择 */}
        <Card>
          <CardHeader>
            <CardTitle>1. 选择模板（可选）</CardTitle>
            <p className="text-sm text-slate-500">
              模板只提供写作提示与示例，不是前置条件；后端不可达时可以直接手写。
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {templatesStatus === 'loading' ? (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
                {[0, 1, 2].map((index) => (
                  <Skeleton key={index} className="h-24 w-full rounded-xl" />
                ))}
                <span className="sr-only">模板加载中…</span>
              </div>
            ) : null}

            {templatesStatus === 'error' ? (
              <ErrorBanner
                message={templatesError ?? '模板列表加载失败，可以无模板继续手写。'}
                onRetry={() => void loadTemplateList()}
              />
            ) : null}

            {templatesStatus === 'ready' ? (
              templates.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {templates.map((tpl) => {
                    const active = templateCode === tpl.code;
                    return (
                      <button
                        key={tpl.code}
                        type="button"
                        onClick={() => void applyTemplate(tpl.code)}
                        className={cn(
                          'rounded-xl border p-3 text-left transition-colors',
                          active
                            ? 'border-ink-950 bg-ink-50'
                            : 'border-ink-200 bg-white hover:border-ink-950 hover:bg-ink-50',
                        )}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium text-slate-900">{tpl.name}</span>
                          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                            {TARGET_LABELS[tpl.target] ?? tpl.target}
                          </span>
                        </span>
                        <span className="mt-1 block text-xs leading-relaxed text-slate-500">
                          {tpl.description}
                        </span>
                        <span className="mt-1 block text-[11px] text-slate-400">
                          {tpl.role_family} · {tpl.section_count} 个区块
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p className="text-sm text-slate-500">暂无可用模板，直接手写即可。</p>
              )
            ) : null}

            {templateLoading ? (
              <p className="flex items-center gap-2 text-xs text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                正在加载模板详情…
              </p>
            ) : null}

            {templateError ? (
              <ErrorBanner
                message={templateError}
                onRetry={() => {
                  if (templateCode) void applyTemplate(templateCode);
                }}
              />
            ) : null}

            {activeTemplate ? (
              <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
                <p className="text-sm font-medium text-slate-900">
                  {activeTemplate.name}
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    {activeTemplate.intro}
                  </span>
                </p>
                {activeTemplate.writing_rules.length > 0 ? (
                  <ul className="space-y-1">
                    {activeTemplate.writing_rules.map((rule, index) => (
                      <li key={index} className="flex gap-2 text-xs leading-relaxed text-slate-600">
                        <span
                          className="mt-[6px] h-1.5 w-1.5 shrink-0 bg-ink-950"
                          aria-hidden
                        />
                        <span>{rule}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <Button size="sm" variant="outline" onClick={handleLoadExample}>
                  <FileText className="h-3.5 w-3.5" aria-hidden />
                  载入模板示例
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {/* 从文档导入 / 载入已有简历 */}
        <Card>
          <CardHeader>
            <CardTitle>2. 从已有简历开始（可选）</CardTitle>
            <p className="text-sm text-ink-500">
              可以直接上传一份现有简历让系统解析填入表单，也可以载入之前上传或保存过的简历。
              编辑器保存过的简历会带着结构化内容，之后能反复打开修改再诊断。
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <Field label="简历标题">
              <Input
                value={title}
                placeholder="例如：张三-后端开发工程师"
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>

            {/* 编辑器内直接上传：不必先回首页走一遍上传流程 */}
            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragActive(false);
                const dropped = event.dataTransfer.files?.[0];
                if (dropped) void importFromFile(dropped);
              }}
              className={cn(
                'flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-dashed px-3.5 py-3 transition-colors duration-200 ease-spring',
                dragActive ? 'border-ink-950 bg-white' : 'border-ink-300 bg-ink-50/70',
              )}
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center bg-ink-950 text-white">
                  <FileUp className="h-4 w-4" aria-hidden strokeWidth={1.75} />
                </span>
                <div>
                  <p className="text-[13px] font-medium text-ink-800">上传文档导入</p>
                  <p className="text-[11px] leading-relaxed text-ink-600">
                    拖到这里或点击选择 · 支持 PDF / DOCX / PNG / JPG / TXT，单份不超过 10MB
                  </p>
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={importing}
                onClick={() => fileInputRef.current?.click()}
              >
                {importing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Upload className="h-3.5 w-3.5" aria-hidden />
                )}
                {importing ? '解析中…' : '选择文件'}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT_ATTRIBUTE}
                className="sr-only"
                aria-label="上传文档导入简历"
                onChange={(event) => {
                  const picked = event.target.files?.[0];
                  // 清空 value，否则连续选择同一个文件不会触发 change
                  event.target.value = '';
                  if (picked) void importFromFile(picked);
                }}
              />
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={handleTogglePicker}>
                <FolderOpen className="h-4 w-4" aria-hidden />
                载入已上传简历
              </Button>
              <Button variant="outline" onClick={handleClearAll}>
                <Trash2 className="h-4 w-4" aria-hidden />
                清空
              </Button>
            </div>

            {pickerOpen ? (
              <div className="space-y-2 rounded-xl border border-slate-200 p-3">
                {resumesStatus === 'loading' ? (
                  <div className="space-y-2" aria-busy="true">
                    <Skeleton className="h-10 w-full" />
                    <Skeleton className="h-10 w-full" />
                    <span className="sr-only">简历列表加载中…</span>
                  </div>
                ) : null}

                {resumesStatus === 'error' ? (
                  <ErrorBanner
                    message={resumesError ?? '简历列表加载失败。'}
                    onRetry={() => void loadResumeList()}
                  />
                ) : null}

                {resumesStatus === 'ready' ? (
                  resumes.length > 0 ? (
                    <>
                      <p className="px-1 text-[11px] text-slate-400">
                        列表包含你上传解析的简历，以及先前在编辑器里保存过的简历。
                      </p>
                      <ul className="max-h-64 space-y-1 overflow-y-auto">
                      {resumes.map((item) => (
                        <li key={item.resume_id}>
                          <button
                            type="button"
                            onClick={() => {
                              setPickerOpen(false);
                              void loadResumeById(item.resume_id);
                            }}
                            className="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50"
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              <span className="truncate font-medium">
                                {item.title || '未命名简历'}
                              </span>
                              <span
                                className={cn(
                                  'shrink-0 rounded px-1.5 py-0.5 text-[11px]',
                                  item.source === 'editor'
                                    ? 'bg-ink-950 text-white'
                                    : 'bg-slate-100 text-slate-500',
                                )}
                              >
                                {item.source === 'editor' ? '编辑器' : '上传'}
                              </span>
                            </span>
                            <span className="text-xs text-slate-400">
                              {[item.parsed_name, item.parsed_title].filter(Boolean).join(' · ') ||
                                '暂无解析信息'}
                            </span>
                          </button>
                        </li>
                      ))}
                      </ul>
                    </>
                  ) : (
                    <p className="text-sm text-slate-500">
                      还没有已上传的简历，先在上传页解析一份，或直接在这里手写。
                    </p>
                  )
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {/* 主体：左表单 + 右预览 */}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,46%)]">
        {/* 左：表单 */}
        <div className="space-y-4 print:hidden">
          <CollapsibleBlock
            title={SECTION_TITLES.basics}
            sectionKey="basics"
            section={sectionMeta.get('basics')}
            filled={isSectionFilled(content, 'basics')}
            open={Boolean(openBlocks.basics)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, basics: !prev.basics }))}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="姓名">
                <Input
                  value={content.basics.name}
                  placeholder="张三"
                  onChange={(event) =>
                    updateContent((prev) => ({
                      ...prev,
                      basics: { ...prev.basics, name: event.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="目标岗位">
                <Input
                  value={content.basics.target_title}
                  placeholder="后端开发工程师"
                  onChange={(event) =>
                    updateContent((prev) => ({
                      ...prev,
                      basics: { ...prev.basics, target_title: event.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="手机">
                <Input
                  value={content.basics.phone}
                  placeholder="138-0000-0000"
                  onChange={(event) =>
                    updateContent((prev) => ({
                      ...prev,
                      basics: { ...prev.basics, phone: event.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="邮箱">
                <Input
                  value={content.basics.email}
                  placeholder="zhangsan@example.com"
                  onChange={(event) =>
                    updateContent((prev) => ({
                      ...prev,
                      basics: { ...prev.basics, email: event.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="所在城市" className="sm:col-span-2">
                <Input
                  value={content.basics.city}
                  placeholder="杭州"
                  onChange={(event) =>
                    updateContent((prev) => ({
                      ...prev,
                      basics: { ...prev.basics, city: event.target.value },
                    }))
                  }
                />
              </Field>
            </div>
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-600">个人链接（作品集 / GitHub / 博客）</p>
              <StringListEditor
                items={content.basics.links}
                itemLabel="这条链接"
                addLabel="添加一条链接"
                placeholder="github.com/zhangsan"
                onChange={(next) =>
                  updateContent((prev) => ({ ...prev, basics: { ...prev.basics, links: next } }))
                }
              />
            </div>
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.summary}
            sectionKey="summary"
            section={sectionMeta.get('summary')}
            filled={isSectionFilled(content, 'summary')}
            open={Boolean(openBlocks.summary)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, summary: !prev.summary }))}
          >
            <Textarea
              value={content.summary}
              rows={3}
              placeholder="3 年后端开发经验，专注交易链路高并发场景，主导过订单系统重构。"
              onChange={(event) =>
                updateContent((prev) => ({ ...prev, summary: event.target.value }))
              }
            />
            <p className="text-right text-[11px] text-slate-400">建议控制在 3 行以内</p>
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.work}
            sectionKey="work"
            section={sectionMeta.get('work')}
            filled={isSectionFilled(content, 'work')}
            open={Boolean(openBlocks.work)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, work: !prev.work }))}
          >
            <div className="space-y-4">
              {content.work.map((item, index) => (
                <div key={index} className="space-y-3 rounded-xl border border-slate-200 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-500">
                      第 {index + 1} 段经历
                    </span>
                    <span className="flex items-center gap-1">
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="上移"
                        title="上移"
                        disabled={index === 0}
                        onClick={() => moveWork(index, -1)}
                      >
                        <ArrowUp className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="下移"
                        title="下移"
                        disabled={index === content.work.length - 1}
                        onClick={() => moveWork(index, 1)}
                      >
                        <ArrowDown className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-slate-400 hover:text-red-600"
                        onClick={() => {
                          if (!confirmRemove('这段工作经历')) return;
                          updateContent((prev) => ({
                            ...prev,
                            work: prev.work.filter((_, i) => i !== index),
                          }));
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        删掉这条
                      </Button>
                    </span>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="公司">
                      <Input
                        value={item.company}
                        placeholder="某科技有限公司"
                        onChange={(event) => updateWork(index, { company: event.target.value })}
                      />
                    </Field>
                    <Field label="岗位">
                      <Input
                        value={item.title}
                        placeholder="后端开发工程师"
                        onChange={(event) => updateWork(index, { title: event.target.value })}
                      />
                    </Field>
                    <Field label="开始时间">
                      <Input
                        value={item.start}
                        placeholder="2022.07"
                        onChange={(event) => updateWork(index, { start: event.target.value })}
                      />
                    </Field>
                    <Field label="结束时间">
                      <Input
                        value={item.end}
                        placeholder="至今"
                        onChange={(event) => updateWork(index, { end: event.target.value })}
                      />
                    </Field>
                    <Field label="城市" className="sm:col-span-2">
                      <Input
                        value={item.location}
                        placeholder="杭州"
                        onChange={(event) => updateWork(index, { location: event.target.value })}
                      />
                    </Field>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-slate-600">工作要点（每行一条）</p>
                    <StringListEditor
                      items={item.highlights}
                      multiline
                      itemLabel="这条要点"
                      addLabel="添加一条要点"
                      placeholder="负责订单核心接口的设计与开发，支撑日均 200 万次调用"
                      onChange={(next) => updateWork(index, { highlights: next })}
                    />
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                onClick={() =>
                  updateContent((prev) => ({
                    ...prev,
                    work: [
                      ...prev.work,
                      { company: '', title: '', start: '', end: '', location: '', highlights: [''] },
                    ],
                  }))
                }
              >
                <Plus className="h-4 w-4" aria-hidden />
                + 添加一段工作经历
              </Button>
            </div>
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.projects}
            sectionKey="projects"
            section={sectionMeta.get('projects')}
            filled={isSectionFilled(content, 'projects')}
            open={Boolean(openBlocks.projects)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, projects: !prev.projects }))}
          >
            <div className="space-y-4">
              {content.projects.map((item, index) => (
                <div key={index} className="space-y-3 rounded-xl border border-slate-200 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-500">
                      第 {index + 1} 个项目
                    </span>
                    <span className="flex items-center gap-1">
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="上移"
                        title="上移"
                        disabled={index === 0}
                        onClick={() => moveProject(index, -1)}
                      >
                        <ArrowUp className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label="下移"
                        title="下移"
                        disabled={index === content.projects.length - 1}
                        onClick={() => moveProject(index, 1)}
                      >
                        <ArrowDown className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-slate-400 hover:text-red-600"
                        onClick={() => {
                          if (!confirmRemove('这个项目')) return;
                          updateContent((prev) => ({
                            ...prev,
                            projects: prev.projects.filter((_, i) => i !== index),
                          }));
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        删掉这条
                      </Button>
                    </span>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="项目名称">
                      <Input
                        value={item.name}
                        placeholder="订单系统重构"
                        onChange={(event) => updateProject(index, { name: event.target.value })}
                      />
                    </Field>
                    <Field label="担任角色">
                      <Input
                        value={item.role}
                        placeholder="核心开发"
                        onChange={(event) => updateProject(index, { role: event.target.value })}
                      />
                    </Field>
                    <Field label="开始时间">
                      <Input
                        value={item.start}
                        placeholder="2023.03"
                        onChange={(event) => updateProject(index, { start: event.target.value })}
                      />
                    </Field>
                    <Field label="结束时间">
                      <Input
                        value={item.end}
                        placeholder="2023.09"
                        onChange={(event) => updateProject(index, { end: event.target.value })}
                      />
                    </Field>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-slate-600">技术栈</p>
                    <TagEditor
                      tags={item.tech}
                      placeholder="Python, Redis"
                      addLabel="添加技术"
                      onChange={(next) => {
                        updateProject(index, { tech: next });
                      }}
                    />
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-slate-600">项目要点（每行一条）</p>
                    <StringListEditor
                      items={item.highlights}
                      multiline
                      itemLabel="这条要点"
                      addLabel="添加一条要点"
                      placeholder="主导订单查询接口重构，P99 延迟从 480ms 降至 120ms"
                      onChange={(next) => updateProject(index, { highlights: next })}
                    />
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                onClick={() =>
                  updateContent((prev) => ({
                    ...prev,
                    projects: [
                      ...prev.projects,
                      { name: '', role: '', start: '', end: '', tech: [], highlights: [''] },
                    ],
                  }))
                }
              >
                <Plus className="h-4 w-4" aria-hidden />
                + 添加一个项目
              </Button>
            </div>
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.education}
            sectionKey="education"
            section={sectionMeta.get('education')}
            filled={isSectionFilled(content, 'education')}
            open={Boolean(openBlocks.education)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, education: !prev.education }))}
          >
            <div className="space-y-4">
              {content.education.map((item, index) => (
                <div key={index} className="space-y-3 rounded-xl border border-slate-200 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-500">
                      第 {index + 1} 段教育经历
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-slate-400 hover:text-red-600"
                      onClick={() => {
                        if (!confirmRemove('这段教育经历')) return;
                        updateContent((prev) => ({
                          ...prev,
                          education: prev.education.filter((_, i) => i !== index),
                        }));
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      删掉这条
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="学校" className="sm:col-span-2">
                      <Input
                        value={item.school}
                        placeholder="某大学"
                        onChange={(event) => updateEducation(index, { school: event.target.value })}
                      />
                    </Field>
                    <Field label="学历">
                      <Input
                        value={item.degree}
                        placeholder="本科"
                        onChange={(event) => updateEducation(index, { degree: event.target.value })}
                      />
                    </Field>
                    <Field label="专业">
                      <Input
                        value={item.major}
                        placeholder="计算机科学与技术"
                        onChange={(event) => updateEducation(index, { major: event.target.value })}
                      />
                    </Field>
                    <Field label="开始时间">
                      <Input
                        value={item.start}
                        placeholder="2018.09"
                        onChange={(event) => updateEducation(index, { start: event.target.value })}
                      />
                    </Field>
                    <Field label="结束时间">
                      <Input
                        value={item.end}
                        placeholder="2022.06"
                        onChange={(event) => updateEducation(index, { end: event.target.value })}
                      />
                    </Field>
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                onClick={() =>
                  updateContent((prev) => ({
                    ...prev,
                    education: [
                      ...prev.education,
                      { school: '', degree: '', major: '', start: '', end: '' },
                    ],
                  }))
                }
              >
                <Plus className="h-4 w-4" aria-hidden />
                + 添加一段教育经历
              </Button>
            </div>
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.skills}
            sectionKey="skills"
            section={sectionMeta.get('skills')}
            filled={isSectionFilled(content, 'skills')}
            open={Boolean(openBlocks.skills)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, skills: !prev.skills }))}
          >
            <TagEditor
              tags={content.skills}
              placeholder="Python, MySQL、Redis"
              addLabel="添加技能"
              onChange={(next) => updateContent((prev) => ({ ...prev, skills: next }))}
            />
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.certificates}
            sectionKey="certificates"
            section={sectionMeta.get('certificates')}
            filled={isSectionFilled(content, 'certificates')}
            open={Boolean(openBlocks.certificates)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, certificates: !prev.certificates }))}
          >
            <StringListEditor
              items={content.certificates}
              itemLabel="这条证书"
              addLabel="添加一条证书"
              placeholder="CET-6"
              onChange={(next) => updateContent((prev) => ({ ...prev, certificates: next }))}
            />
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.awards}
            sectionKey="awards"
            section={sectionMeta.get('awards')}
            filled={isSectionFilled(content, 'awards')}
            open={Boolean(openBlocks.awards)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, awards: !prev.awards }))}
          >
            <StringListEditor
              items={content.awards}
              itemLabel="这条荣誉"
              addLabel="添加一条荣誉奖项"
              placeholder="校级一等奖学金"
              onChange={(next) => updateContent((prev) => ({ ...prev, awards: next }))}
            />
          </CollapsibleBlock>

          <CollapsibleBlock
            title={SECTION_TITLES.custom}
            sectionKey="custom"
            section={sectionMeta.get('custom')}
            filled={isSectionFilled(content, 'custom')}
            open={Boolean(openBlocks.custom)}
            onToggle={() => setOpenBlocks((prev) => ({ ...prev, custom: !prev.custom }))}
          >
            <div className="space-y-4">
              {content.custom_sections.map((section, index) => (
                <div key={index} className="space-y-3 rounded-xl border border-slate-200 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-500">
                      第 {index + 1} 个自定义区块
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-slate-400 hover:text-red-600"
                      onClick={() => {
                        if (!confirmRemove('这个自定义区块')) return;
                        updateContent((prev) => ({
                          ...prev,
                          custom_sections: prev.custom_sections.filter((_, i) => i !== index),
                        }));
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      删掉这条
                    </Button>
                  </div>
                  <Field label="区块标题（为空时该区块不渲染）">
                    <Input
                      value={section.title}
                      placeholder="开源贡献"
                      onChange={(event) =>
                        updateContent((prev) => ({
                          ...prev,
                          custom_sections: prev.custom_sections.map((item, i) =>
                            i === index ? { ...item, title: event.target.value } : item,
                          ),
                        }))
                      }
                    />
                  </Field>
                  <div className="space-y-2">
                    <p className="text-xs font-medium text-slate-600">区块内容（每行一条）</p>
                    <StringListEditor
                      items={section.items}
                      multiline
                      itemLabel="这条内容"
                      addLabel="添加一条内容"
                      placeholder="xxx-gateway：负责限流模块设计与维护"
                      onChange={(next) =>
                        updateContent((prev) => ({
                          ...prev,
                          custom_sections: prev.custom_sections.map((item, i) =>
                            i === index ? { ...item, items: next } : item,
                          ),
                        }))
                      }
                    />
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                onClick={() =>
                  updateContent((prev) => ({
                    ...prev,
                    custom_sections: [...prev.custom_sections, { title: '', items: [''] }],
                  }))
                }
              >
                <Plus className="h-4 w-4" aria-hidden />
                + 添加一个自定义区块
              </Button>
            </div>
          </CollapsibleBlock>

          {/* 保存与导出 */}
          <Card>
            <CardHeader>
              <CardTitle>3. 保存与导出</CardTitle>
              <p className="text-sm text-slate-500">
                导出按简历 ID 生成，点击后会先把最新内容保存到服务端，再开始下载。
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {/* 用 button 而不是 <a download>：下载必须带 X-Device-Id 请求头，
                    只有 JS 的 fetch 能带，原生导航做不到（会 404）。 */}
                <Button
                  type="button"
                  variant="outline"
                  disabled={Boolean(exporting)}
                  onClick={() => void runExport('markdown')}
                >
                  {exporting === 'markdown' ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <ClipboardCopy className="h-4 w-4" aria-hidden />
                  )}
                  保存并导出 Markdown
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={Boolean(exporting)}
                  onClick={() => void runExport('docx')}
                >
                  {exporting === 'docx' ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Download className="h-4 w-4" aria-hidden />
                  )}
                  保存并导出 DOCX
                </Button>
                <Button
                  variant="outline"
                  disabled={exporting !== null}
                  onClick={() => void runExport('pdf')}
                >
                  {exporting === 'pdf' ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Download className="h-4 w-4" aria-hidden />
                  )}
                  保存并导出 PDF
                </Button>
              </div>
              <p className="text-[11px] leading-relaxed text-slate-400">
                {savedResumeId
                  ? `已保存简历 ID：${savedResumeId}${upToDate ? '（与当前内容一致）' : '（内容已修改，导出时会重新保存）'}`
                  : '尚未保存：点击任意导出按钮即会先保存。'}
                {' '}PDF 依赖服务端转换组件，不可用时请用 DOCX，或按 Ctrl+P 打印为 PDF。
              </p>
            </CardContent>
          </Card>

          {/* 发起诊断 */}
          <Card>
            <CardHeader>
              <CardTitle>4. 用这份简历发起诊断</CardTitle>
              <p className="text-sm text-slate-500">
                粘贴目标岗位 JD（至少 {JD_MIN_LENGTH} 字），依次完成保存简历 → 解析 JD → 创建任务。
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  value={jdTitle}
                  disabled={busy}
                  placeholder="岗位名称（选填），如：后端开发工程师"
                  onChange={(event) => setJdTitle(event.target.value)}
                />
                <Input
                  value={company}
                  disabled={busy}
                  placeholder="公司名称（选填）"
                  onChange={(event) => setCompany(event.target.value)}
                />
              </div>
              <Textarea
                value={jdText}
                rows={7}
                disabled={busy}
                placeholder={'岗位职责：……\n任职要求：……\n例如：熟悉 Python / MySQL / Redis，有高并发系统经验优先。'}
                onChange={(event) => setJdText(event.target.value)}
              />
              <p className="text-right text-xs text-slate-400">{jdText.trim().length} 字</p>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  size="lg"
                  disabled={busy || jdText.trim().length < JD_MIN_LENGTH}
                  onClick={() => void handleDiagnose()}
                >
                  {busy ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      {PHASE_TEXT[phase as Exclude<Phase, 'idle'>]}
                    </>
                  ) : (
                    <>
                      <Sparkles className="h-4 w-4" aria-hidden />
                      用这份简历发起诊断
                    </>
                  )}
                </Button>
                {busy ? (
                  <span className="text-xs text-slate-500">请勿关闭页面，任务创建后会自动跳转。</span>
                ) : (
                  <span className="text-xs text-slate-400">
                    JD 不足 {JD_MIN_LENGTH} 字时按钮不可用，避免匹配分析失真。
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 右：实时预览 */}
        <div className="space-y-3 lg:sticky lg:top-20 lg:self-start print:static print:top-0">
          <div className="flex flex-wrap items-center justify-between gap-2 print:hidden">
            <h2 className="text-sm font-semibold text-slate-900">实时预览</h2>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => void handleCopyMarkdown()}>
                {copyState === 'copied' ? (
                  <Check className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <ClipboardCopy className="h-3.5 w-3.5" aria-hidden />
                )}
                {copyState === 'copied'
                  ? '已复制'
                  : copyState === 'failed'
                    ? '复制失败，请手动选择'
                    : '复制 Markdown'}
              </Button>
              <Button size="sm" variant="outline" onClick={() => window.print()}>
                <Printer className="h-3.5 w-3.5" aria-hidden />
                打印 / 存为 PDF
              </Button>
            </div>
          </div>
          <div className="print-area lg:max-h-[calc(100vh-8rem)] lg:overflow-y-auto print:max-h-none print:overflow-visible">
            <ResumePreview content={content} />
          </div>
          <p className="text-[11px] leading-relaxed text-slate-400 print:hidden">
            打印时会自动隐藏表单与导航，只输出右侧这份简历（可在打印对话框里选择「另存为 PDF」）。
          </p>
        </div>
      </div>

      {busy ? (
        <p className="sr-only" aria-live="polite">
          {PHASE_TEXT[phase as Exclude<Phase, 'idle'>]}
        </p>
      ) : null}
    </div>
  );
}

export default EditorWorkspace;