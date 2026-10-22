'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  ChevronDown,
  ClipboardCopy,
  Download,
  FileText,
  LayoutTemplate,
  Lightbulb,
  Loader2,
  Plus,
  Printer,
  Save,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import {
  ApiError,
  createJd,
  createResumeFromEditor,
  createTask,
  deleteResume,
  downloadResumeExport,
  getResume,
  getTemplate,
  listTemplates,
  updateResume,
} from '@/lib/api';
import type {
  EditorContent,
  EditorEducationItem,
  EditorProjectItem,
  EditorWorkItem,
  ExportFormat,
  TemplateDetail,
  TemplateSection,
  TemplateSectionKey,
  TemplateSummary,
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
import {
  clearEditorDraft,
  readEditorDraft,
  saveEditorDraft,
} from '@/lib/editor-draft';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { ErrorBanner } from '@/components/states';
import { ResumePreview } from '@/components/editor/resume-preview';
import type { TemplateDesignId } from '@/components/editor/template-resume';
import TemplateDeck from '@/components/editor/template-deck';

/* ------------------------------------------------------------------ */
/* 常量                                                                */
/* ------------------------------------------------------------------ */

const JD_MIN_LENGTH = 20;

const PDF_UNAVAILABLE_HINT =
  '服务端未配置 PDF 转换组件，请改用 DOCX，或按 Ctrl+P 用浏览器打印为 PDF';


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
            <p className="border-l-2 border-ink-950 bg-ink-50 px-3 py-2 text-[11.5px] leading-relaxed text-ink-600">
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
  const avatarInputRef = React.useRef<HTMLInputElement>(null);
  const [avatarError, setAvatarError] = React.useState<string | null>(null);

  const [templates, setTemplates] = React.useState<TemplateSummary[]>([]);
  const [templatesStatus, setTemplatesStatus] = React.useState<Status>('loading');
  const [templatesError, setTemplatesError] = React.useState<string | null>(null);
  const [activeTemplate, setActiveTemplate] = React.useState<TemplateDetail | null>(null);
  const [templateLoading, setTemplateLoading] = React.useState(false);
  const [templateError, setTemplateError] = React.useState<string | null>(null);

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
  const [fitExporting, setFitExporting] = React.useState(false);
  const [savingToAccount, setSavingToAccount] = React.useState(false);
  const [deletingResume, setDeletingResume] = React.useState(false);
  const [deckOpen, setDeckOpen] = React.useState(false);
  const [copyState, setCopyState] = React.useState<'idle' | 'copied' | 'failed'>('idle');
  const copyTimer = React.useRef<number | null>(null);

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

  const loadResumeById = React.useCallback(
    async (resumeId: string, templateOverride: string | null = null) => {
      setError(null);
      setNotice(null);
      try {
        const resume = await getResume(resumeId);
        const nextContent = resume.editor_content
          ? normalizeEditorContent(resume.editor_content)
          : editorContentFromParsed(resume.parsed);
        const nextTitle = resume.title || defaultResumeTitle(nextContent);
        // 开始页显式选的模板优先；否则编辑器保存的简历沿用 template_code，
        // 上传件没有结构化模板，保持无模板。
        const nextTemplateCode =
          templateOverride ??
          (resume.source === 'editor' ? (resume.template_code ?? null) : null);

        setContent(nextContent);
        setTitle(nextTitle);
        setTemplateCode(nextTemplateCode);
        if (nextTemplateCode) {
          void applyTemplate(nextTemplateCode);
        } else {
          setActiveTemplate(null);
          setTemplateLoading(false);
          setTemplateError(null);
        }

        /**
         * 上传件（没有 editor_content）不能沿用它的 resume_id：
         * 后端导出/保存只有在简历带 editor_content 时才是无损的，直接复用上传件
         * 会让第一次导出退化为"以解析结果为源"的有损渲染。和旧版编辑器内导入一致，
         * 这里松开 ID，下一次保存/导出会另存一份带完整结构化内容的可编辑版本。
         */
        const fromUpload = !resume.editor_content;
        setSavedResumeId(fromUpload ? null : resume.resume_id);
        // 用回填后的 templateCode 计算快照，保证刚载入时不会因为模板差异被判成“未保存”。
        setSavedKey(
          fromUpload ? null : snapshotKey(nextTitle, nextTemplateCode, nextContent),
        );
        setNotice(
          resume.editor_content
            ? `已载入这份简历的编辑器内容${
                templateOverride ? '，并套用了你在上一步选择的模板' : ''
              }；修改后保存会更新同一份简历，可以直接再次发起诊断。`
            : '这份简历来自上传文件，已把解析结果填入编辑器；个人优势、荣誉奖项、自定义区块需要你补充。确认后保存/导出会另存为一份带完整结构化内容的可编辑版本。',
        );
      } catch (err) {
        setError(describeError(err));
      }
    },
    [applyTemplate],
  );

  React.useEffect(() => {
    void loadTemplateList();
  }, [loadTemplateList]);

  /* ---------------- 初始化：起点 + 模板 + 草稿 ---------------- */

  /**
   * 查询参数由「开始页」生成：
   * - `start=blank`  → 空白表单（不恢复草稿）；
   * - `start=draft`  → 恢复本机草稿；
   * - `start=resume` → 载入 `resumeId`（可被 `template` 覆盖版式）；
   * - 无参数（直接访问 / 旧书签）→ 有草稿就恢复，否则空白。
   */
  React.useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;

    const start = searchParams.get('start');
    const paramResumeId = searchParams.get('resumeId');
    const paramTemplate = searchParams.get('template');
    const wantsDraft = start === 'draft' || (!start && !paramResumeId);

    // 非法参数兜底：start=resume 却没有 resumeId，与其给一个空白编辑器，不如回开始页重选。
    if (start === 'resume' && !paramResumeId) {
      router.replace('/editor');
      return;
    }

    /**
     * 深链 / 旧书签进入 `start=blank|resume` 时，开始页的确认流程被绕过了：
     * 先把本机草稿有内容这件事告诉用户，避免"点了继续编辑，未保存草稿被静默覆盖"。
     * 用户取消则回开始页，草稿仍在，可以从容选择「继续编辑草稿」。
     */
    if (!wantsDraft) {
      const existing = readEditorDraft();
      const draftHasContent = existing
        ? countFilledSections(existing.content) > 0 ||
          existing.title.trim().length > 0 ||
          Boolean(existing.savedResumeId)
        : false;
      if (
        draftHasContent &&
        !window.confirm('本机还有一份未保存的草稿，打开这份简历会覆盖它。要继续吗？')
      ) {
        router.replace('/editor');
        return;
      }
    }

    const draft = wantsDraft ? readEditorDraft() : null;
    if (draft) {
      setTitle(draft.title);
      setTemplateCode(draft.templateCode);
      setContent(draft.content);
      setSavedResumeId(draft.savedResumeId ?? null);
      // 草稿里带着简历 ID 时，用同一份快照做基准，避免恢复后「未保存」状态误报；
      // 没有 ID 的草稿本来就需要新建，savedKey 保持 null。
      setSavedKey(
        draft.savedResumeId
          ? snapshotKey(draft.title, draft.templateCode, draft.content)
          : null,
      );
      setDraftRestored(true);
      setDraftSavedAt(draft.savedAt ?? null);
      if (paramTemplate) void applyTemplate(paramTemplate);
      else if (draft.templateCode) void applyTemplate(draft.templateCode);
    } else if (paramResumeId) {
      void loadResumeById(paramResumeId, paramTemplate);
    } else {
      if (paramTemplate) void applyTemplate(paramTemplate);
      else {
        setActiveTemplate(null);
        setTemplateLoading(false);
        setTemplateError(null);
      }
    }

    setDraftReady(true);

    /**
     * URL 归一化：进入编辑器后清掉 `start` / `resumeId` / `template`。
     * 否则用户一刷新，`start=blank` 会再次"跳过草稿"，把刚写的内容换成空白；
     * 归一化之后刷新走「有草稿就恢复草稿」的默认分支。
     */
    if (start || paramResumeId || paramTemplate) {
      router.replace('/editor/write', { scroll: false });
    }
  }, [applyTemplate, loadResumeById, router, searchParams]);

  /* ---------------- 草稿自动保存（防抖 500ms） ---------------- */

  /**
   * 空草稿不值得存：没有标题、没有任何区块内容、也没关联服务端简历时，
   * 存下来只会让开始页出现一条「已填 0/9」的假草稿。清空操作也靠这条规则
   * 把旧草稿真正删掉，而不是又写回一份空壳。
   */
  const draftHasValue =
    title.trim().length > 0 || filledCount > 0 || savedResumeId !== null;

  React.useEffect(() => {
    if (!draftReady) return;
    const timer = window.setTimeout(() => {
      if (!draftHasValue) {
        clearEditorDraft();
        setDraftSavedAt(null);
        return;
      }
      const savedAt = saveEditorDraft({ title, templateCode, content, savedResumeId });
      if (savedAt) setDraftSavedAt(savedAt);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [content, draftHasValue, draftReady, savedResumeId, templateCode, title]);

  /**
   * 刷新 / 关闭页面时立刻落一次草稿。
   * 防抖窗口是 500ms，若用户刚改完就刷新，只靠定时器会丢掉最后一次修改。
   */
  React.useEffect(() => {
    if (!draftReady) return;
    const flush = () => {
      if (!draftHasValue) {
        clearEditorDraft();
        return;
      }
      saveEditorDraft({ title, templateCode, content, savedResumeId });
    };
    window.addEventListener('beforeunload', flush);
    return () => window.removeEventListener('beforeunload', flush);
  }, [content, draftHasValue, draftReady, savedResumeId, templateCode, title]);

  React.useEffect(() => {
    return () => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  /* ---------------- 内容编辑 ---------------- */

  const updateContent = React.useCallback((updater: (prev: EditorContent) => EditorContent) => {
    setContent((prev) => updater(prev));
  }, []);

  /** 读图 → 压缩到 480px 内 → data URL。避免把几 MB 原图塞进简历 JSON。 */
  const handleAvatarPick = async (file: File | null) => {
    if (!file) return;
    setAvatarError(null);
    if (!file.type.startsWith('image/')) {
      setAvatarError('请选择图片文件（PNG / JPG）');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setAvatarError('图片不能超过 5MB');
      return;
    }
    try {
      const bitmap = await createImageBitmap(file);
      const maxSide = 480;
      const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('当前浏览器不支持图片处理');
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close?.();
      const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
      updateContent((prev) => ({ ...prev, basics: { ...prev.basics, avatar: dataUrl } }));
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : '图片处理失败，请换一张试试');
    }
  };

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
    // 保留用户当前选中的模板 code；不能回退到 activeTemplate?.code，
    // 否则模板详情加载失败时会把已选模板一起清掉。
    setTemplateCode(templateCode);
    setSavedResumeId(null);
    setSavedKey(null);
    setNotice('已清空，可以从空白开始手写。');
  };

  /** 移除当前版式，退回 ATS 单栏预览（开始页也能做同样的选择）。 */
  const handleClearTemplate = () => {
    setTemplateCode(null);
    setActiveTemplate(null);
    setTemplateError(null);
    setTemplateLoading(false);
    setNotice('已移除模板，右侧预览切回 ATS 单栏版式。');
  };

  const handleDiscardDraft = () => {
    clearEditorDraft();
    setContent(createEmptyEditorContent());
    setTitle('');
    setTemplateCode(null);
    setActiveTemplate(null);
    setTemplateLoading(false);
    setTemplateError(null);
    setSavedResumeId(null);
    setSavedKey(null);
    setDraftRestored(false);
    setDraftSavedAt(null);
    setNotice('已放弃上次的草稿。');
  };

  /* ---------------- 保存 / 导出 ---------------- */

  /**
   * 把当前编辑器内容落到服务端：
   * - 已有 `savedResumeId` → PUT 更新同一份简历（不会每改一次就新建一份）；
   * - 没有 → POST 新建一份，拿到 ID 后后续操作都走更新；
   * - 若 PUT 返回 404（简历在别处被删了）→ 清掉本地 ID 后重新新建一次，避免一直失败。
   * 保存后刷新「载入已有简历」列表，让新标题/来源及时可见。
   */
  const ensureResumeSaved = React.useCallback(async (): Promise<string> => {
    const key = snapshotKey(title, templateCode, content);
    if (savedResumeId && savedKey === key) return savedResumeId;
    const finalTitle = title.trim() || defaultResumeTitle(content);
    const normalized = normalizeEditorContent(content);
    const nextKey = snapshotKey(finalTitle, templateCode, content);

    if (savedResumeId) {
      try {
        const resume = await updateResume(savedResumeId, {
          title: finalTitle,
          template_code: templateCode,
          content: normalized,
        });
        setSavedResumeId(resume.resume_id);
        setSavedKey(nextKey);
        if (!title.trim()) setTitle(finalTitle);
        return resume.resume_id;
      } catch (err) {
        // 404 = 这份简历已被删除（换设备 / 在账号中心删掉）：本地 ID 作废，下面走一次新建。
        if (!(err instanceof ApiError && err.code === 'RESUME_NOT_FOUND')) throw err;
        setSavedResumeId(null);
        setSavedKey(null);
      }
    }

    const resume = await createResumeFromEditor({
      title: finalTitle,
      template_code: templateCode,
      content: normalized,
    });
    setSavedResumeId(resume.resume_id);
    setSavedKey(nextKey);
    if (!title.trim()) setTitle(finalTitle);
    return resume.resume_id;
  }, [content, savedKey, savedResumeId, templateCode, title]);

  /** 「保存到账号」：只保存、不下载；文案明确告诉用户是更新同一份而不是新建。 */
  const handleSaveToAccount = async () => {
    if (savingToAccount || phase !== 'idle') return;
    const wasSaved = Boolean(savedResumeId);
    setSavingToAccount(true);
    setError(null);
    setNotice(null);
    try {
      const resumeId = await ensureResumeSaved();
      setNotice(
        wasSaved
          ? `已更新到账号（简历 ID：${resumeId}），之后继续修改保存的仍是这一份。`
          : `已保存到账号（简历 ID：${resumeId}）。刷新或换设备后可以在「账号中心 → 我的简历」里继续编辑。`,
      );
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSavingToAccount(false);
    }
  };

  /** 删除当前这份已保存简历：确认 → DELETE → 清空本地状态（避免下次保存又更新一个已删 ID）。 */
  const handleDeleteSavedResume = async () => {
    if (!savedResumeId || deletingResume) return;
    if (!window.confirm('删除后无法恢复，确定删除这份已保存的简历吗？')) return;

    setDeletingResume(true);
    setError(null);
    setNotice(null);
    try {
      await deleteResume(savedResumeId);
      setSavedResumeId(null);
      setSavedKey(null);
      clearEditorDraft();
      setContent(createEmptyEditorContent());
      setTitle('');
      setDraftRestored(false);
      setDraftSavedAt(null);
      setNotice('已删除这份简历，编辑器已重置为空白内容。');
    } catch (err) {
      setError(describeError(err));
    } finally {
      setDeletingResume(false);
    }
  };

  const runExport = async (format: ExportFormat, options: { fit?: 'none' | 'one' } = {}) => {
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
      const { blob, contentDisposition, pageCount, fitScale } = await downloadResumeExport(
        resumeId,
        format,
        options,
      );

      const extension = format === 'markdown' ? 'md' : format;
      const filename =
        parseFilename(contentDisposition) ||
        `${title.trim() || defaultResumeTitle(content)}.${extension}`;
      triggerDownload(URL.createObjectURL(blob), filename);

      const labels: Record<ExportFormat, string> = { markdown: 'Markdown', docx: 'DOCX', pdf: 'PDF' };
      if (options.fit === 'one') {
        if (pageCount === 1) {
          setNotice(
            `已压缩到 1 页 A4 并开始下载 PDF（字号缩放到 ${Math.round((fitScale ?? 1) * 100)}%）。`,
          );
        } else if (pageCount && pageCount > 1) {
          setNotice(
            `内容偏多：即使缩到 ${Math.round((fitScale ?? 1) * 100)}% 仍有 ${pageCount} 页，建议删减部分经历或要点后再试。`,
          );
        } else {
          setNotice('已保存并开始下载压缩版 PDF。');
        }
      } else {
        setNotice(`已保存并开始下载 ${labels[format]} 文件。`);
      }
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
      {/* 顶部：返回开始页 + 标题 + 完成度 */}
      <div className="space-y-4 print:hidden">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5">
            <Link
              href="/editor"
              className="inline-flex items-center gap-1 text-[11.5px] font-medium text-ink-500 transition-colors hover:text-ink-950"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              重新选择模板与起点
            </Link>
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
              <Sparkles className="h-5 w-5 text-ink-950" aria-hidden />
              简历编辑器
            </h1>
            <p className="text-sm text-slate-600">
              左侧按结构化字段填写，右侧实时预览；可以直接导出，也可以用这份简历发起一次诊断。
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

        {/* 写作设置条：标题 + 模板。开始页的选择在这里仍可更换 / 移除，不用退回上一步 */}
        <div className="panel flex flex-wrap items-end justify-between gap-x-4 gap-y-3 px-4 py-3">
          <label className="block w-full min-w-[220px] max-w-md space-y-1">
            <span className="text-[11px] font-medium text-slate-600">简历标题</span>
            <Input
              value={title}
              placeholder="例如：张三-后端开发工程师"
              onChange={(event) => setTitle(event.target.value)}
              className="h-9"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2 pb-0.5">
            <span className="inline-flex items-center gap-1.5 border border-ink-300 bg-white px-2 py-1 text-[11px] text-ink-600">
              <LayoutTemplate className="h-3.5 w-3.5 text-ink-500" aria-hidden />
              模板：
              <span className="font-semibold text-ink-950">
                {activeTemplate
                  ? activeTemplate.name
                  : templateCode
                    ? templateError
                      ? '加载失败'
                      : '加载中…'
                    : '不使用'}
              </span>
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDeckOpen(true)}
              disabled={templatesStatus !== 'ready' || templates.length === 0}
            >
              <LayoutTemplate className="h-3.5 w-3.5" aria-hidden />
              {activeTemplate ? '更换模板' : '选择模板'}
            </Button>
            {activeTemplate ? (
              <>
                <Button size="sm" variant="ghost" onClick={handleLoadExample}>
                  <FileText className="h-3.5 w-3.5" aria-hidden />
                  载入示例
                </Button>
                <Button size="sm" variant="ghost" onClick={handleClearTemplate}>
                  移除模板
                </Button>
              </>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              className="text-ink-500 hover:text-red-700"
              onClick={handleClearAll}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              清空
            </Button>
          </div>
        </div>

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

        {templatesStatus === 'error' ? (
          <p className="text-[11px] leading-relaxed text-ink-500">
            模板列表暂时不可用（{templatesError ?? '加载失败'}），不影响继续填写；稍后可在开始页重新选择。
          </p>
        ) : null}

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
      </div>

      {/* 主体：左表单 + 右预览 */}
      <div className="editor-layout grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,46%)]">
        {/* 左：表单 */}
        <div className="min-w-0 space-y-4 print:hidden">
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

              <Field label="头像（可选）" className="sm:col-span-2">
                <div className="flex flex-wrap items-center gap-3">
                  {content.basics.avatar ? (
                    // eslint-disable-next-line @next/next/no-img-element -- data URL 头像，next/image 不适用
                    <img
                      src={content.basics.avatar}
                      alt="头像预览"
                      className="h-16 w-16 border border-ink-200 object-cover"
                    />
                  ) : (
                    <span className="flex h-16 w-16 items-center justify-center border border-dashed border-ink-300 text-[10px] text-ink-400">
                      未上传
                    </span>
                  )}
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => avatarInputRef.current?.click()}
                    >
                      选择图片
                    </Button>
                    {content.basics.avatar ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          updateContent((prev) => ({ ...prev, basics: { ...prev.basics, avatar: '' } }))
                        }
                      >
                        移除
                      </Button>
                    ) : null}
                  </div>
                  <input
                    ref={avatarInputRef}
                    type="file"
                    accept="image/png,image/jpeg"
                    className="hidden"
                    onChange={(event) => {
                      void handleAvatarPick(event.target.files?.[0] ?? null);
                      event.target.value = '';
                    }}
                  />
                </div>
                <p className="mt-1 text-[10.5px] leading-relaxed text-ink-400">
                  仅用于导出 PDF / DOCX 时嵌入，不参与诊断评分（避免照片带来的偏见）。
                </p>
                {avatarError ? <p className="mt-1 text-[10.5px] text-red-600">{avatarError}</p> : null}
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
              <CardTitle>保存与导出</CardTitle>
              <p className="text-sm text-slate-500">
                导出按简历 ID 生成，点击后会先把最新内容保存到服务端，再开始下载。
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* 明确的「保存到账号」入口：不下载，只落库。
                  有 savedResumeId 时走 PUT 更新同一份，避免每次修改都新建一份简历。 */}
              <div className="flex flex-wrap items-center gap-3 border border-slate-200 bg-slate-50/70 px-3 py-2.5">
                <Button
                  type="button"
                  disabled={savingToAccount || Boolean(exporting) || phase !== 'idle'}
                  onClick={() => void handleSaveToAccount()}
                >
                  {savingToAccount ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Save className="h-4 w-4" aria-hidden />
                  )}
                  {savingToAccount ? '保存中…' : '保存到账号'}
                </Button>
                <p className="max-w-sm text-[11px] leading-relaxed text-slate-500">
                  已保存的简历会更新同一份，不会重复新建；下次可以在「账号中心 → 我的简历」里继续编辑。
                </p>
              </div>

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
                  disabled={exporting !== null || fitExporting}
                  onClick={() => void runExport('pdf')}
                >
                  {exporting === 'pdf' && !fitExporting ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Download className="h-4 w-4" aria-hidden />
                  )}
                  保存并导出 ATS 单栏 PDF
                </Button>
                <Button
                  variant="outline"
                  disabled={exporting !== null || fitExporting}
                  onClick={async () => {
                    if (fitExporting) return;
                    setFitExporting(true);
                    try {
                      await runExport('pdf', { fit: 'one' });
                    } finally {
                      setFitExporting(false);
                    }
                  }}
                >
                  {fitExporting ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <FileText className="h-4 w-4" aria-hidden />
                  )}
                  压缩到一页 PDF
                </Button>
                {/* 版式 PDF：不走服务端，直接把右侧预览交给浏览器打印（可另存为 PDF）。
                    与上面两个服务端导出并列展示，让用户清楚「好看的那份」和「ATS 那份」的区别。 */}
                <Button
                  type="button"
                  variant="secondary"
                  disabled={phase !== 'idle'}
                  onClick={() => window.print()}
                >
                  <Printer className="h-4 w-4" aria-hidden />
                  导出版式 PDF（打印 / 另存为）
                </Button>
              </div>
              <p className="text-[11px] leading-relaxed text-ink-600">
                版式 PDF 与右侧预览完全一致（含颜色、分栏与页首色块）；DOCX 与 ATS 单栏 PDF
                保持纯单栏文本版式，便于招聘系统解析。
              </p>
              <p className="text-[11px] leading-relaxed text-slate-400">
                {savedResumeId
                  ? `已保存简历 ID：${savedResumeId}${
                      upToDate
                        ? '（与当前内容一致，再次保存会更新这一份）'
                        : '（内容已修改，保存时会更新这一份）'
                    }`
                  : '尚未保存到账号：点击「保存到账号」或任意导出按钮即可保存。'}
                {' '}PDF 依赖服务端转换组件，不可用时请用 DOCX，或按 Ctrl+P 打印为 PDF。
              </p>

              {/* 删除当前已保存简历。只放在表单区（父级 print:hidden），不会出现在打印输出里。 */}
              {savedResumeId ? (
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3">
                  <p className="text-[11px] leading-relaxed text-slate-400">
                    删除后这份简历不再出现在「我的简历」里，已生成的诊断报告仍会保留。
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="border-red-200 text-red-700 hover:bg-red-50 hover:text-red-800"
                    disabled={deletingResume || phase !== 'idle'}
                    onClick={() => void handleDeleteSavedResume()}
                  >
                    {deletingResume ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                    )}
                    {deletingResume ? '删除中…' : '删除这份简历'}
                  </Button>
                </div>
              ) : null}
            </CardContent>
          </Card>

          {/* 发起诊断 */}
          <Card>
            <CardHeader>
              <CardTitle>用这份简历发起诊断</CardTitle>
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
        <div className="min-w-0 space-y-3 lg:sticky lg:top-20 lg:self-start print:static print:top-0">
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
            <ResumePreview
              content={content}
              design={activeTemplate?.design as TemplateDesignId | undefined}
            />
          </div>
          <p className="text-[11px] leading-relaxed text-slate-400 print:hidden">
            打印时会自动隐藏表单与导航，只输出右侧这份简历。在打印对话框里选「另存为 PDF」，
纸张 A4、边距「无」、并关闭「页眉和页脚」，即可得到与预览一致的版式 PDF。
          </p>
        </div>
      </div>

      {busy ? (
        <p className="sr-only" aria-live="polite">
          {PHASE_TEXT[phase as Exclude<Phase, 'idle'>]}
        </p>
      ) : null}

      {/* 模板画廊：整屏卡面滚动，关掉后回到编辑器 */}
      <TemplateDeck
        open={deckOpen}
        templates={templates}
        activeCode={templateCode}
        onSelect={(code) => {
          void applyTemplate(code);
          setDeckOpen(false);
        }}
        onClose={() => setDeckOpen(false)}
      />
    </div>
  );
}

export default EditorWorkspace;