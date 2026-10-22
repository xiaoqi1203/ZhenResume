'use client';

/**
 * 简历编辑「开始页」（路由 `/editor`）。
 *
 * 产品动机（来自用户反馈）：
 * 旧版 `/editor` 把「选模板」「从已有简历开始」两张卡片直接堆在编辑表单上方，
 * 用户一进页面就要面对一个已经展开的长表单 + 两个前置选项，很难判断"现在该做什么"。
 *
 * 新信息架构：
 * 1. 本页只负责两个可选决策 —— 模板、内容起点，全部放在**一张卡面**里，一眼能看完；
 * 2. 决策完成后跳转 `/editor/write`（真正的编辑器），编辑器里只保留写作本身；
 * 3. 本机草稿不静默恢复，而是在卡面顶部显式给出「继续上次草稿」，避免用户
 *    一进编辑器就被旧内容覆盖了「想新建」的意图。
 *
 * 设计约束：沿用站内既有视觉世界（#FAFAFA 画布 / 白底 panel / 1px 发丝线 /
 * 近黑标题 / 紫色仅用于强调），不引入新的卡片语言或阴影体系。
 */

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  Check,
  FileText,
  FolderOpen,
  LayoutTemplate,
  Loader2,
  Trash2,
  UploadCloud,
  type LucideIcon,
} from 'lucide-react';

import { ApiError, listResumes, listTemplates, uploadResume } from '@/lib/api';
import type { ResumeSummary, TemplateSummary } from '@/lib/types';
import {
  clearEditorDraft,
  readEditorDraft,
  type EditorDraft,
} from '@/lib/editor-draft';
import { countFilledSections, SECTION_TOTAL } from '@/lib/resume-content';
import { uploadGuardMessage } from '@/lib/upload-guard';
import { Button } from '@/components/ui/button';
import { ErrorBanner, EmptyState } from '@/components/states';
import { Skeleton } from '@/components/ui/skeleton';
import UploadDropzone, { validateResumeFile } from '@/components/upload-dropzone';
import TemplateDeck, {
  LAYOUT_LABELS,
  STYLE_LABELS,
  resolveDesign,
  resolveLayout,
  resolveStyle,
} from '@/components/editor/template-deck';
import {
  ScaledTemplatePreview,
  type TemplateDesignId,
} from '@/components/editor/template-resume';
import { TEMPLATE_PREVIEW_CONTENT } from '@/lib/template-sample';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ */
/* 类型与常量                                                          */
/* ------------------------------------------------------------------ */

type Status = 'loading' | 'ready' | 'error';
type StartMode = 'blank' | 'upload' | 'saved';
type Phase = 'idle' | 'uploading' | 'redirecting';

const RESUME_LIMIT = 20;

const START_TABS: {
  value: StartMode;
  label: string;
  hint: string;
  icon: LucideIcon;
}[] = [
  { value: 'blank', label: '空白新建', hint: '从零填写', icon: FileText },
  { value: 'upload', label: '上传文档', hint: '解析现有简历', icon: UploadCloud },
  { value: 'saved', label: '我的简历', hint: '载入已保存的', icon: FolderOpen },
];

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '操作失败，请稍后重试';
}

/** 拼 `/editor/write` 的查询串；空值不写入，URL 保持干净可读。 */
function editorWriteHref(input: {
  start: 'blank' | 'draft' | 'resume';
  templateCode?: string | null;
  resumeId?: string | null;
}): string {
  const params = new URLSearchParams();
  params.set('start', input.start);
  if (input.resumeId) params.set('resumeId', input.resumeId);
  if (input.templateCode) params.set('template', input.templateCode);
  return `/editor/write?${params.toString()}`;
}

function formatSavedAt(savedAt: string | undefined): string {
  if (!savedAt) return '保存时间未知';
  const date = new Date(savedAt);
  if (Number.isNaN(date.getTime())) return '保存时间未知';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

/* ------------------------------------------------------------------ */
/* 模板卡片                                                            */
/* ------------------------------------------------------------------ */

function SelectionBadge() {
  return (
    <span className="absolute right-2 top-2 inline-flex items-center gap-1 bg-ink-950 px-1.5 py-0.5 text-[10px] font-semibold text-white">
      <Check className="h-2.5 w-2.5" aria-hidden />
      已选
    </span>
  );
}

function TemplateTile({
  template,
  selected,
  onSelect,
}: {
  template: TemplateSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const design = resolveDesign(template);
  const style = resolveStyle(template);
  const layout = resolveLayout(template);
  const accent = (template.accent ?? '').trim();

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`选择模板：${template.name}`}
      className={cn(
        'group flex flex-col border bg-white text-left transition-colors duration-200 ease-snap',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-950',
        selected ? 'border-ink-950' : 'border-ink-200 hover:border-ink-950',
      )}
    >
      <div className="relative border-b border-ink-200 bg-ink-100/60 p-1.5">
        <ScaledTemplatePreview
          content={TEMPLATE_PREVIEW_CONTENT}
          design={design as TemplateDesignId | undefined}
          avatarPlaceholder
          className="border border-ink-200"
        />
        {selected ? <SelectionBadge /> : null}
      </div>
      <div className="flex flex-1 items-start justify-between gap-2 p-2.5">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-semibold text-ink-950">{template.name}</p>
          <p className="mt-0.5 text-[10.5px] text-ink-500">
            {STYLE_LABELS[style]} · {LAYOUT_LABELS[layout]}
          </p>
        </div>
        {accent ? (
          <span
            aria-hidden
            title={`强调色 ${accent}`}
            className="mt-0.5 h-2.5 w-2.5 shrink-0 border border-black/10"
            style={{ backgroundColor: accent }}
          />
        ) : null}
      </div>
    </button>
  );
}

/** 「不使用模板」—— 不是空选项，而是明确的「ATS 单栏文本」版式。 */
function NoTemplateTile({ selected, onSelect }: { selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label="不使用模板"
      className={cn(
        'group flex flex-col border bg-white text-left transition-colors duration-200 ease-snap',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-950',
        selected ? 'border-ink-950' : 'border-ink-200 hover:border-ink-950',
      )}
    >
      <div className="relative flex aspect-[210/297] flex-col justify-center border-b border-ink-200 bg-ink-50 px-4">
        <div className="space-y-1.5" aria-hidden>
          <span className="block h-2 w-1/2 bg-ink-300" />
          <span className="block h-px w-full bg-ink-200" />
          <span className="block h-1 w-5/6 bg-ink-200" />
          <span className="block h-1 w-4/6 bg-ink-200" />
          <span className="mt-4 block h-px w-full bg-ink-200" />
          <span className="block h-1 w-5/6 bg-ink-200" />
          <span className="block h-1 w-3/6 bg-ink-200" />
        </div>
        {selected ? <SelectionBadge /> : null}
      </div>
      <div className="flex flex-1 items-start p-2.5">
        <div className="min-w-0">
          <p className="text-[12px] font-semibold text-ink-950">不使用模板</p>
          <p className="mt-0.5 text-[10.5px] text-ink-500">ATS 单栏 · 最稳妥</p>
        </div>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* 已保存简历行                                                        */
/* ------------------------------------------------------------------ */

function ResumeRow({
  resume,
  selected,
  onSelect,
}: {
  resume: ResumeSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const meta = [resume.parsed_name, resume.parsed_title].filter(Boolean).join(' · ');
  const created = formatCreatedAt(resume.created_at);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2.5 text-left transition-colors duration-150',
        selected ? 'bg-ink-50' : 'hover:bg-ink-50/70',
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center border',
            selected ? 'border-ink-950 bg-ink-950 text-white' : 'border-ink-300 bg-white',
          )}
        >
          {selected ? <Check className="h-3 w-3" aria-hidden /> : null}
        </span>
        <span className="truncate text-[12.5px] font-semibold text-ink-950">
          {resume.title || '未命名简历'}
        </span>
        <span
          className={cn(
            'shrink-0 px-1.5 py-0.5 text-[10px] font-semibold',
            resume.source === 'editor' ? 'bg-ink-950 text-white' : 'bg-ink-100 text-ink-600',
          )}
        >
          {resume.source === 'editor' ? '编辑器' : '上传'}
        </span>
      </span>
      <span className="truncate text-[11px] text-ink-500">
        {meta || '暂无解析信息'}
        {created ? ` · ${created}` : ''}
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* 主组件                                                              */
/* ------------------------------------------------------------------ */

export interface EditorStartProps {
  /** 兼容旧链接 `/editor?template=xxx`：进入开始页时预选该模板。 */
  initialTemplateCode?: string | null;
}

export function EditorStart({ initialTemplateCode = null }: EditorStartProps) {
  const router = useRouter();

  const [draft, setDraft] = React.useState<EditorDraft | null>(null);
  const [draftLoaded, setDraftLoaded] = React.useState(false);

  const [templateCode, setTemplateCode] = React.useState<string | null>(initialTemplateCode);
  const [templates, setTemplates] = React.useState<TemplateSummary[]>([]);
  const [templatesStatus, setTemplatesStatus] = React.useState<Status>('loading');
  const [templatesError, setTemplatesError] = React.useState<string | null>(null);
  const [deckOpen, setDeckOpen] = React.useState(false);

  const [startMode, setStartMode] = React.useState<StartMode>('blank');

  const [resumes, setResumes] = React.useState<ResumeSummary[]>([]);
  const [resumesStatus, setResumesStatus] = React.useState<Status>('loading');
  const [resumesError, setResumesError] = React.useState<string | null>(null);
  const [selectedResumeId, setSelectedResumeId] = React.useState<string | null>(null);

  const [pendingFile, setPendingFile] = React.useState<File | null>(null);
  const [fileError, setFileError] = React.useState<string | null>(null);

  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);

  const busy = phase !== 'idle';

  /* ---------------- 数据加载 ---------------- */

  const loadTemplates = React.useCallback(async () => {
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

  const loadResumes = React.useCallback(async () => {
    setResumesStatus('loading');
    setResumesError(null);
    try {
      const data = await listResumes({ limit: RESUME_LIMIT });
      setResumes(data.items);
      setResumesStatus('ready');
    } catch (err) {
      setResumesError(describeError(err));
      setResumesStatus('error');
    }
  }, []);

  React.useEffect(() => {
    setDraft(readEditorDraft());
    setDraftLoaded(true);
  }, []);

  React.useEffect(() => {
    void loadTemplates();
    void loadResumes();
  }, [loadResumes, loadTemplates]);

  const selectedResume = React.useMemo(
    () => resumes.find((item) => item.resume_id === selectedResumeId) ?? null,
    [resumes, selectedResumeId],
  );

  const selectedTemplate = React.useMemo(
    () => templates.find((item) => item.code === templateCode) ?? null,
    [templateCode, templates],
  );

  const draftFilled = draft ? countFilledSections(draft.content) : 0;

  /* ---------------- 草稿 ---------------- */

  const continueDraft = () => {
    if (busy) return;
    router.push(editorWriteHref({ start: 'draft' }));
  };

  const discardDraft = () => {
    if (!window.confirm('删除本机保存的草稿？此操作不可恢复。')) return;
    clearEditorDraft();
    setDraft(null);
  };

  /**
   * 本机有草稿时，任何"从别处开始"的动作都会在编辑器里覆盖它。
   * 这里明确问一次；上传失败时不删除草稿（只有成功进入编辑器后才交给新内容）。
   */
  const confirmReplaceDraft = (): boolean => {
    if (!draft) return true;
    return window.confirm(
      '本机还有一份未保存的草稿，开始新的编辑会覆盖它。要继续吗？',
    );
  };

  const forgetDraft = () => {
    clearEditorDraft();
    setDraft(null);
  };

  /* ---------------- 进入编辑器 ---------------- */

  const enterBlank = () => {
    if (busy) return;
    if (!confirmReplaceDraft()) return;
    if (draft) forgetDraft();
    router.push(editorWriteHref({ start: 'blank', templateCode }));
  };

  const enterSaved = () => {
    if (busy || !selectedResumeId) return;
    if (!confirmReplaceDraft()) return;
    if (draft) forgetDraft();
    router.push(
      editorWriteHref({ start: 'resume', resumeId: selectedResumeId, templateCode }),
    );
  };

  const uploadAndEnter = async () => {
    if (busy) return;
    const file = pendingFile;
    if (!file) return;

    const problem = validateResumeFile(file);
    if (problem) {
      setFileError(problem);
      return;
    }
    if (!confirmReplaceDraft()) return;

    setError(null);
    setPhase('uploading');
    try {
      const resume = await uploadResume(file, file.name);
      // 抽不到文字（扫描件 / 空文件）时不进编辑器：填一份空表单没有意义，
      // 与首页诊断入口使用同一道闸门，避免用户以为"导入成功"。
      const blocked = uploadGuardMessage(resume);
      if (blocked) {
        setError(`${blocked} 也可以切到「空白新建」直接手写。`);
        setPhase('idle');
        return;
      }
      if (draft) forgetDraft();
      setPhase('redirecting');
      router.push(
        editorWriteHref({ start: 'resume', resumeId: resume.resume_id, templateCode }),
      );
    } catch (err) {
      setError(describeError(err));
      setPhase('idle');
    }
  };

  const handlePrimary = () => {
    if (startMode === 'blank') {
      enterBlank();
      return;
    }
    if (startMode === 'saved') {
      enterSaved();
      return;
    }
    void uploadAndEnter();
  };

  /** tablist 的方向键 / Home / End 切换（ARIA tabs 模式）。 */
  const handleStartTabKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const order: StartMode[] = ['blank', 'upload', 'saved'];
    const index = order.indexOf(startMode);
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (index + 1) % order.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (index - 1 + order.length) % order.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = order.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const next = order[nextIndex];
    setStartMode(next);
    window.requestAnimationFrame(() =>
      document.getElementById(`start-tab-${next}`)?.focus(),
    );
  };

  /* ---------------- 摘要文案 ---------------- */

  const templateLabel = templateCode
    ? (selectedTemplate?.name ?? '已选模板')
    : '不使用模板';

  const startLabel = (() => {
    if (startMode === 'blank') return '从空白开始';
    if (startMode === 'saved') {
      return selectedResume ? `载入《${selectedResume.title || '未命名简历'}》` : '尚未选择简历';
    }
    return pendingFile ? `导入《${pendingFile.name}》` : '尚未选择文件';
  })();

  const primaryLabel = (() => {
    if (phase === 'uploading') return '正在上传并解析…';
    if (phase === 'redirecting') return '正在进入编辑器…';
    if (startMode === 'upload') {
      return pendingFile ? '上传解析并进入编辑器' : '请先选择简历文件';
    }
    if (startMode === 'saved' && !selectedResumeId) return '请先选择一份简历';
    return '进入编辑器';
  })();

  const primaryDisabled =
    busy ||
    (startMode === 'upload' && !pendingFile) ||
    (startMode === 'saved' && !selectedResumeId);

  /* ---------------- 渲染 ---------------- */

  return (
    <div className="space-y-6">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-[26px] leading-tight tracking-tight text-ink-950 sm:text-[32px]">
          开始一份简历
        </h1>
        <p className="text-[13px] leading-relaxed text-ink-600">
          两个选择都可以跳过：模板决定版式与写作提示，起点决定内容从哪里来。
          决定之后进入编辑器，左填右看，随时可以回来换模板或导入另一份简历。
        </p>
      </header>

      {/* 两个决定放在同一张卡面里：先选版式，再选自哪里开始，最后统一进入编辑器 */}
      <section className="panel overflow-hidden" aria-labelledby="start-card-heading">
        <h2 id="start-card-heading" className="sr-only">
          简历起点设置
        </h2>

        {/* 本机草稿：显式入口，不再静默恢复 */}
        {draftLoaded && draft ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 bg-brand-50/40 px-5 py-3 sm:px-6">
            <div className="min-w-0">
              <p className="text-[12.5px] font-semibold text-ink-950">发现上次未完成的草稿</p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-ink-600">
                {formatSavedAt(draft.savedAt)} 自动保存 · 已填 {draftFilled}/{SECTION_TOTAL} 个区块
                {draft.savedResumeId ? ' · 已关联账号里的一份简历' : ''}
                。继续草稿会恢复上次的模板与内容。
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button size="sm" onClick={continueDraft} disabled={busy}>
                继续编辑草稿
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </Button>
              <Button size="sm" variant="ghost" onClick={discardDraft} disabled={busy}>
                放弃草稿
              </Button>
            </div>
          </div>
        ) : null}

        {/* ---------- 1 / 选择模板（可选） ---------- */}
        <div className="px-5 py-5 sm:px-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-bold tracking-tight text-ink-950">
                1 · 选择模板
                <span className="ml-2 text-[12px] font-normal text-ink-500">可选</span>
              </h2>
              <p className="mt-1 text-[12px] leading-relaxed text-ink-600">
                模板决定纸面版式与区块写作提示，不改变你填写的内容；编辑页里仍可随时更换。
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDeckOpen(true)}
              disabled={templatesStatus !== 'ready' || templates.length === 0}
            >
              <LayoutTemplate className="h-3.5 w-3.5" aria-hidden />
              查看全部模板
              {templatesStatus === 'ready' && templates.length > 0
                ? `（${templates.length}）`
                : ''}
            </Button>
          </div>

          <div className="mt-4">
            {templatesStatus === 'loading' ? (
              <div
                className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
                aria-busy="true"
              >
                {[0, 1, 2, 3].map((index) => (
                  <div
                    key={index}
                    className="aspect-[210/297] animate-pulse border border-ink-200 bg-ink-100/60"
                  />
                ))}
                <span className="sr-only">模板加载中…</span>
              </div>
            ) : null}

            {templatesStatus === 'error' ? (
              <div className="space-y-3">
                <ErrorBanner
                  message={templatesError ?? '模板列表加载失败，可以无模板继续。'}
                  onRetry={() => void loadTemplates()}
                />
                <NoTemplateTile
                  selected={templateCode === null}
                  onSelect={() => setTemplateCode(null)}
                />
                <p className="text-[11px] text-ink-500">
                  模板服务不可用不影响写简历：选「不使用模板」即可继续下一步。
                </p>
              </div>
            ) : null}

            {templatesStatus === 'ready' ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                <NoTemplateTile
                  selected={templateCode === null}
                  onSelect={() => setTemplateCode(null)}
                />
                {templates.map((template) => (
                  <TemplateTile
                    key={template.code}
                    template={template}
                    selected={template.code === templateCode}
                    onSelect={() => setTemplateCode(template.code)}
                  />
                ))}
              </div>
            ) : null}
          </div>

          <p className="mt-3 text-[11px] leading-relaxed text-ink-500">
            当前选择：
            <span className="font-semibold text-ink-950">{templateLabel}</span>
            {selectedTemplate?.description ? (
              <span className="text-ink-500"> · {selectedTemplate.description}</span>
            ) : null}
          </p>
        </div>

        {/* ---------- 2 / 从已有简历开始（可选） ---------- */}
        <div className="border-t border-ink-200 px-5 py-5 sm:px-6">
          <h2 className="text-[15px] font-bold tracking-tight text-ink-950">
            2 · 从已有简历开始
            <span className="ml-2 text-[12px] font-normal text-ink-500">可选</span>
          </h2>
          <p className="mt-1 text-[12px] leading-relaxed text-ink-600">
            没有现成简历就从空白开始；有的话直接导入，省去重写。载入已保存简历会沿用它原来的模板，
            如果你在上面选了模板，以你选的为准。
          </p>

          <div
            className="mt-4 border border-ink-200"
            role="tablist"
            aria-label="选择简历起点"
            onKeyDown={handleStartTabKeyDown}
          >
            <div className="grid grid-cols-1 divide-y divide-ink-200 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
              {START_TABS.map((tab) => {
                const Icon = tab.icon;
                const selected = startMode === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    role="tab"
                    id={`start-tab-${tab.value}`}
                    aria-selected={selected}
                    aria-controls={`start-panel-${tab.value}`}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => setStartMode(tab.value)}
                    disabled={busy}
                    className={cn(
                      'flex items-start gap-2.5 px-3.5 py-2.5 text-left transition-colors duration-150',
                      'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand-500',
                      selected
                        ? 'bg-ink-950 text-white'
                        : 'bg-white text-ink-700 hover:bg-ink-50 disabled:opacity-60',
                    )}
                  >
                    <Icon
                      className={cn(
                        'mt-0.5 h-4 w-4 shrink-0',
                        selected ? 'text-white' : 'text-ink-500',
                      )}
                      aria-hidden
                    />
                    <span className="min-w-0">
                      <span className="block text-[12.5px] font-semibold">{tab.label}</span>
                      <span
                        className={cn(
                          'mt-0.5 block text-[10.5px] leading-tight',
                          selected ? 'text-white/70' : 'text-ink-500',
                        )}
                      >
                        {tab.hint}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt-3">
            <div hidden={startMode !== 'blank'}>
              <div
                role="tabpanel"
                id="start-panel-blank"
                aria-labelledby="start-tab-blank"
                className="border border-dashed border-ink-300 bg-ink-50/60 px-4 py-3"
              >
                <p className="text-[12px] leading-relaxed text-ink-600">
                  从空白表单开始。建议先填「个人信息 + 个人优势 + 工作经历」这三块，
                  其余区块可以随时补；内容会自动保存在本机浏览器。
                </p>
              </div>
            </div>

            <div hidden={startMode !== 'upload'}>
              <div role="tabpanel" id="start-panel-upload" aria-labelledby="start-tab-upload">
                <UploadDropzone
                  file={pendingFile}
                  onFileChange={(file) => {
                    setPendingFile(file);
                    setFileError(null);
                    setError(null);
                  }}
                  onError={setFileError}
                  disabled={busy}
                  hint="支持 PDF / DOCX / PNG / JPG / TXT，单份不超过 10MB；解析后进入编辑器继续修改"
                />
                {fileError ? <p className="mt-2 text-[12px] text-red-600">{fileError}</p> : null}
                <p className="mt-2 text-[11px] leading-relaxed text-ink-500">
                  解析得到的个人信息、经历、技能会填入编辑器；个人优势、荣誉奖项等解析不到的区块
                  需要你补充。
                </p>
              </div>
            </div>

            <div hidden={startMode !== 'saved'}>
              <div role="tabpanel" id="start-panel-saved" aria-labelledby="start-tab-saved">
                {resumesStatus === 'loading' ? (
                  <div className="space-y-px border border-ink-200" aria-busy="true">
                    {[0, 1, 2].map((index) => (
                      <Skeleton key={index} className="h-12 w-full" />
                    ))}
                    <span className="sr-only">简历列表加载中…</span>
                  </div>
                ) : null}

                {resumesStatus === 'error' ? (
                  <ErrorBanner
                    message={resumesError ?? '简历列表加载失败。'}
                    onRetry={() => void loadResumes()}
                  />
                ) : null}

                {resumesStatus === 'ready' ? (
                  resumes.length > 0 ? (
                    <>
                      <div className="max-h-72 divide-y divide-ink-200 overflow-y-auto border border-ink-200">
                        {resumes.map((item) => (
                          <ResumeRow
                            key={item.resume_id}
                            resume={item}
                            selected={item.resume_id === selectedResumeId}
                            onSelect={() => setSelectedResumeId(item.resume_id)}
                          />
                        ))}
                      </div>
                      <p className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-500">
                        <span>
                          列表包含上传解析的简历，以及先前在编辑器里保存过的简历。
                        </span>
                        <Link
                          href="/user"
                          className="font-semibold text-ink-950 underline-offset-4 hover:underline"
                        >
                          在账号中心管理全部简历 →
                        </Link>
                      </p>
                    </>
                  ) : (
                    <EmptyState
                      title="账号里还没有可载入的简历"
                      description="先上传一份现有简历导入，或直接用空白表单手写；保存到账号后就能在这里继续编辑。"
                      action={
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setStartMode('upload')}
                        >
                          上传文档导入
                        </Button>
                      }
                    />
                  )
                ) : null}
              </div>
            </div>
          </div>
        </div>

        {/* ---------- 统一入口 ---------- */}
        <div className="border-t border-ink-200 bg-ink-50/70 px-5 py-4 sm:px-6">
          {error ? (
            <div className="mb-3">
              <ErrorBanner message={error} onRetry={() => setError(null)} />
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="min-w-0 text-[12px] leading-relaxed text-ink-600">
              即将进入编辑器：
              <span className="mx-1.5 font-semibold text-ink-950">{templateLabel}</span>
              <span aria-hidden className="text-ink-300">
                /
              </span>
              <span className="ml-1.5">{startLabel}</span>
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="lg"
                onClick={handlePrimary}
                disabled={primaryDisabled}
                aria-busy={busy}
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : startMode === 'saved' ? (
                  <FolderOpen className="h-4 w-4" aria-hidden />
                ) : startMode === 'upload' ? (
                  <UploadCloud className="h-4 w-4" aria-hidden />
                ) : (
                  <FileText className="h-4 w-4" aria-hidden />
                )}
                {primaryLabel}
                {!primaryDisabled ? (
                  <ArrowRight className="h-4 w-4" aria-hidden />
                ) : null}
              </Button>
              {startMode === 'upload' && !pendingFile ? (
                <span className="text-[11px] text-ink-500">先在左侧选择或拖入文件</span>
              ) : null}
              {startMode === 'saved' && !selectedResumeId ? (
                <span className="text-[11px] text-ink-500">先在上面选中一份简历</span>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      <p className="flex items-center gap-1.5 text-[11px] leading-relaxed text-ink-500">
        <Trash2 className="h-3.5 w-3.5 shrink-0 text-ink-400" aria-hidden />
        编辑器里的内容会自动保存在本机浏览器；点「保存到账号」才会同步到账号，
        之后可以在账号中心继续编辑。
      </p>

      <TemplateDeck
        open={deckOpen}
        templates={templates}
        activeCode={templateCode}
        onSelect={(code) => {
          setTemplateCode(code);
          setDeckOpen(false);
        }}
        onClose={() => setDeckOpen(false)}
      />
    </div>
  );
}

export default EditorStart;