'use client';

/**
 * 简历模板画廊（全屏 modal）。
 *
 * 交互结构：
 * 1. **顶部固定区**：标题「模板库」+ 模板数量 + 关闭；风格 tabs / 场景 / 布局三重筛选。
 * 2. **卡片网格**：lg 4 列 / sm 2 列 / 手机 1 列。缩略图不是截图，而是
 *    `ScaledTemplatePreview` 把真实 A4 版式等比缩放后渲染 —— 和用户最终拿到的
 *    版式 PDF 是同一份 DOM，不会出现「预览一张图、导出另一张图」。
 * 3. **详情层**：点卡片或「查看模板介绍」才请求 `GET /templates/{code}`，
 *    避免打开画廊就下载 4 份完整 `example_content`；左侧用该模板自带的示例内容
 *    渲染大图，右侧是描述 / 标签 / 版式介绍 / 写作规则 / 区块结构。
 *
 * 版式元数据（style / layout / design / tags）在后端是新加字段，老数据可能没有；
 * 这里一律通过 `resolve*` 系列函数兜底，缺字段不影响筛选与缩略图。
 */

import * as React from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, Check, RotateCcw, X } from 'lucide-react';

import { ApiError, getTemplate } from '@/lib/api';
import { EmptyState, ErrorBanner } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ScaledTemplatePreview,
  type TemplateDesignId,
} from '@/components/editor/template-resume';
import { TEMPLATE_PREVIEW_CONTENT } from '@/lib/template-sample';
import type {
  TemplateDetail,
  TemplateLayout,
  TemplateStyle,
  TemplateSummary,
  TemplateTarget,
} from '@/lib/types';
import { cn } from '@/lib/utils';

/* ------------------------------------------------------------------ */
/* 版式元数据字典与兜底                                                 */
/* ------------------------------------------------------------------ */

export const STYLE_LABELS: Record<TemplateStyle, string> = {
  minimal: '简约',
  business: '商务',
  fresh: '清新',
  creative: '个性',
};

export const LAYOUT_LABELS: Record<TemplateLayout, string> = {
  single: '单栏',
  sidebar: '双栏',
};

/** 后端 design → style 的兜底映射（字段缺失的老模板也能正确参与风格筛选）。 */
const DESIGN_STYLE_FALLBACK: Record<string, TemplateStyle> = {
  celadon: 'minimal',
  navy: 'business',
  ivory: 'fresh',
  cobalt: 'business',
};

/** `TemplateDesignId` 的运行时白名单：未知 design 交给 `ScaledTemplatePreview` 回落 celadon。 */
const KNOWN_DESIGNS: readonly TemplateDesignId[] = ['celadon', 'navy', 'ivory', 'cobalt'];

/** 详情层展示给用户看的设计色名（不直接暴露英文 design id）。 */
const DESIGN_LABELS: Record<TemplateDesignId, string> = {
  celadon: '青绿',
  navy: '深蓝',
  ivory: '象牙',
  cobalt: '钴蓝',
};

const TARGET_FALLBACK_TAGS: Record<TemplateTarget, string> = {
  campus: '校招',
  experienced: '社招',
  transition: '社招',
};

export function resolveStyle(template: TemplateSummary): TemplateStyle {
  const style = template.style;
  if (style && STYLE_LABELS[style]) return style;
  const byDesign = template.design ? DESIGN_STYLE_FALLBACK[template.design] : undefined;
  return byDesign ?? 'minimal';
}

export function resolveLayout(template: TemplateSummary): TemplateLayout {
  const layout = template.layout;
  if (layout === 'single' || layout === 'sidebar') return layout;
  return 'single';
}

export function resolveDesign(template: TemplateSummary): TemplateDesignId | undefined {
  const design = template.design;
  return design && (KNOWN_DESIGNS as readonly string[]).includes(design)
    ? (design as TemplateDesignId)
    : undefined;
}

function resolveTags(template: TemplateSummary): string[] {
  if (template.tags && template.tags.length > 0) return template.tags;
  return [TARGET_FALLBACK_TAGS[template.target] ?? '通用', LAYOUT_LABELS[resolveLayout(template)]];
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '模板详情加载失败，请稍后重试';
}

/* ------------------------------------------------------------------ */
/* 筛选选项                                                            */
/* ------------------------------------------------------------------ */

type StyleFilter = TemplateStyle | 'all';
type LayoutFilter = TemplateLayout | 'all';
type SceneFilter = 'all' | 'experienced' | 'intern' | 'campus';

const STYLE_TABS: { value: StyleFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'minimal', label: '简约' },
  { value: 'business', label: '商务' },
  { value: 'fresh', label: '清新' },
  { value: 'creative', label: '个性' },
];

/** 场景按 `tags` 匹配（后端标签是中文「社招 / 实习 / 校招」）。 */
const SCENE_OPTIONS: { value: SceneFilter; label: string; tag: string | null }[] = [
  { value: 'all', label: '全部场景', tag: null },
  { value: 'experienced', label: '社招', tag: '社招' },
  { value: 'intern', label: '实习', tag: '实习' },
  { value: 'campus', label: '校招', tag: '校招' },
];

const LAYOUT_OPTIONS: { value: LayoutFilter; label: string }[] = [
  { value: 'all', label: '全部布局' },
  { value: 'single', label: '单栏' },
  { value: 'sidebar', label: '双栏' },
];

/* ------------------------------------------------------------------ */
/* 卡片                                                                */
/* ------------------------------------------------------------------ */

function TemplateCard({
  template,
  active,
  onOpen,
  onSelect,
}: {
  template: TemplateSummary;
  active: boolean;
  onOpen: () => void;
  onSelect: () => void;
}) {
  const design = resolveDesign(template);
  const style = resolveStyle(template);
  const layout = resolveLayout(template);
  const tags = resolveTags(template);
  const accent = (template.accent ?? '').trim();

  return (
    <article
      aria-label={`${template.name}模板卡片`}
      onClick={onOpen}
      className={cn(
        'group flex cursor-pointer flex-col border bg-white transition-colors duration-200 ease-snap',
        active ? 'border-ink-950' : 'border-ink-200 hover:border-ink-950',
      )}
    >
      {/* 缩略图：真实 A4 版式等比缩放，不是截图 */}
      <div className="relative border-b border-ink-200 bg-ink-100/60 p-2">
        <ScaledTemplatePreview
          content={TEMPLATE_PREVIEW_CONTENT}
          design={design}
          avatarPlaceholder
          className="border border-ink-200"
        />

        {active ? (
          <span className="absolute left-3.5 top-3.5 inline-flex items-center gap-1 bg-ink-950 px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em] text-white">
            <Check className="h-3 w-3" aria-hidden />
            当前使用
          </span>
        ) : null}

        {/* hover 才出现的「使用这个模板」：直接选中，不进入详情 */}
        <div className="absolute inset-0 flex items-center justify-center bg-ink-950/45 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100">
          <Button
            size="sm"
            variant="secondary"
            disabled={active}
            className="bg-white"
            onClick={(event) => {
              event.stopPropagation();
              onSelect();
            }}
          >
            {active ? (
              <>
                <Check className="h-3.5 w-3.5" aria-hidden />
                当前使用中
              </>
            ) : (
              '使用这个模板'
            )}
          </Button>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-[15px] font-bold tracking-tight text-ink-950">{template.name}</h3>
          {accent ? (
            <span
              aria-hidden
              title={`强调色 ${accent}`}
              className="mt-1 h-2.5 w-2.5 shrink-0 border border-black/10"
              style={{ backgroundColor: accent }}
            />
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10.5px] text-ink-500">
          <span className="border border-ink-300 px-1.5 py-0.5">{LAYOUT_LABELS[layout]}</span>
          <span className="border border-ink-300 px-1.5 py-0.5">{STYLE_LABELS[style]}</span>
          <span>{template.section_count} 个区块</span>
        </div>

        <p className="line-clamp-2 text-[12px] leading-relaxed text-ink-600">
          {template.description}
        </p>

        <div className="flex flex-wrap gap-1">
          {tags.slice(0, 2).map((tag) => (
            <span key={tag} className="bg-ink-100 px-1.5 py-0.5 text-[10.5px] text-ink-600">
              {tag}
            </span>
          ))}
          {tags.length > 2 ? (
            <span className="bg-ink-100 px-1.5 py-0.5 text-[10.5px] text-ink-600">
              +{tags.length - 2}
            </span>
          ) : null}
        </div>

        <div className="mt-auto pt-1">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onOpen();
            }}
            className="inline-flex items-center gap-1 text-[12px] font-semibold text-ink-700 transition-colors hover:text-ink-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-950 group-hover:text-ink-950"
          >
            查看模板介绍
            <ArrowRight
              className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5"
              aria-hidden
            />
          </button>
        </div>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* 详情层                                                              */
/* ------------------------------------------------------------------ */

type DetailStatus = 'idle' | 'loading' | 'ready' | 'error';

interface DetailState {
  status: DetailStatus;
  detail: TemplateDetail | null;
  error: string | null;
}

const EMPTY_DETAIL: DetailState = { status: 'idle', detail: null, error: null };

function DetailMeta({
  detail,
}: {
  detail: TemplateDetail;
}) {
  const design = resolveDesign(detail);
  const accent = (detail.accent ?? '').trim();
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-600">
      <span className="border border-ink-300 px-2 py-0.5">{LAYOUT_LABELS[resolveLayout(detail)]}</span>
      <span className="border border-ink-300 px-2 py-0.5">{STYLE_LABELS[resolveStyle(detail)]}</span>
      <span className="border border-ink-300 px-2 py-0.5">{detail.section_count} 个区块</span>
      {accent ? (
        <span className="inline-flex items-center gap-1.5 border border-ink-300 px-2 py-0.5">
          <span
            aria-hidden
            className="h-2.5 w-2.5 border border-black/10"
            style={{ backgroundColor: accent }}
          />
          强调色 · {design ? DESIGN_LABELS[design] : '默认'}
        </span>
      ) : null}
    </div>
  );
}

function TemplateDetailLayer({
  state,
  activeCode,
  onClose,
  onRetry,
  onUse,
}: {
  state: DetailState;
  activeCode: string | null;
  onClose: () => void;
  onRetry: () => void;
  /** 选中当前详情里的模板（父组件负责 applyTemplate + 关闭画廊）。 */
  onUse: (code: string) => void;
}) {
  const detail = state.detail;
  const active = detail ? detail.code === activeCode : false;
  const design = detail ? resolveDesign(detail) : undefined;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-ink-950/50 p-3 print:hidden sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label="模板详情"
      onClick={onClose}
    >
      <div
        className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden border border-ink-950 bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-ink-200 px-4 py-3 sm:px-5">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <h3 className="truncate text-base font-bold tracking-tight text-ink-950">
              {detail?.name ??
                (state.status === 'loading' ? '正在加载模板详情…' : '模板详情')}
            </h3>
            {detail ? (
              <span className="text-[11px] text-ink-500">
                {STYLE_LABELS[resolveStyle(detail)]} · {LAYOUT_LABELS[resolveLayout(detail)]}
              </span>
            ) : null}
          </div>
          <Button size="sm" variant="ghost" autoFocus onClick={onClose} aria-label="关闭模板详情">
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>

        <div className="grid flex-1 gap-5 overflow-y-auto p-4 sm:p-5 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-7">
          {/* 左：大预览（用该模板自带的 example_content 渲染） */}
          <div className="self-start border border-ink-200 bg-ink-100/60 p-2.5 sm:p-3">
            {state.status === 'loading' ? (
              <Skeleton className="aspect-[210/297] w-full bg-white" />
            ) : null}
            {detail ? (
              <ScaledTemplatePreview content={detail.example_content} design={design} avatarPlaceholder />
            ) : null}
            {state.status === 'error' ? (
              <div className="flex aspect-[210/297] w-full items-center justify-center bg-white p-6 text-center text-[12px] leading-relaxed text-ink-400">
                版式预览不可用，请稍后重试
              </div>
            ) : null}
          </div>

          {/* 右：模板介绍 / 写作规则 / 区块结构 */}
          <div className="space-y-5">
            {state.status === 'error' ? (
              <ErrorBanner
                message={state.error ?? '模板详情加载失败'}
                onRetry={onRetry}
              />
            ) : null}

            {state.status === 'loading' ? (
              <div className="space-y-3" aria-busy="true">
                <Skeleton className="h-5 w-1/2" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-11/12" />
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-32 w-full" />
                <span className="sr-only">模板详情加载中…</span>
              </div>
            ) : null}

            {detail ? (
              <>
                <div className="space-y-3">
                  <p className="text-[13px] leading-relaxed text-ink-600">{detail.description}</p>
                  <DetailMeta detail={detail} />
                  <div className="flex flex-wrap gap-1">
                    {resolveTags(detail).map((tag) => (
                      <span
                        key={tag}
                        className="bg-ink-100 px-2 py-0.5 text-[10.5px] text-ink-600"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 border-y border-ink-200 py-3">
                  <Button
                    disabled={active}
                    // 与卡片 hover 按钮共用父组件的 onSelect 入口，避免两处状态分叉。
                    onClick={() => onUse(detail.code)}
                  >
                    {active ? (
                      <>
                        <Check className="h-4 w-4" aria-hidden />
                        当前使用中
                      </>
                    ) : (
                      '使用这个模板'
                    )}
                  </Button>
                  <span className="text-[11px] leading-relaxed text-ink-400">
                    {active ? '当前预览已经在用这套版式' : '选择后会关闭画廊并应用该版式'}
                  </span>
                </div>

                <section>
                  <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-500">
                    版式介绍
                  </h4>
                  <p className="mt-2 text-[13px] leading-relaxed text-ink-700">{detail.intro}</p>
                </section>

                {detail.writing_rules.length > 0 ? (
                  <section>
                    <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-500">
                      写作规则
                    </h4>
                    <ol className="mt-2 space-y-1.5">
                      {detail.writing_rules.map((rule, index) => (
                        <li
                          key={index}
                          className="flex gap-2 text-[12.5px] leading-relaxed text-ink-600"
                        >
                          <span className="mt-[2px] font-mono text-[10px] text-ink-400">
                            {String(index + 1).padStart(2, '0')}
                          </span>
                          <span>{rule}</span>
                        </li>
                      ))}
                    </ol>
                  </section>
                ) : null}

                {detail.sections.length > 0 ? (
                  <section>
                    <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-500">
                      区块结构（{detail.sections.length}）
                    </h4>
                    <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {detail.sections.map((section) => (
                        <div key={section.key} className="border border-ink-200 px-2.5 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[12px] font-medium text-ink-800">
                              {section.title}
                            </span>
                            {section.required ? (
                              <span className="shrink-0 text-[10px] text-ink-500">建议填写</span>
                            ) : null}
                          </div>
                          <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-ink-400">
                            {section.tip}
                          </p>
                        </div>
                      ))}
                    </div>
                  </section>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 主组件                                                              */
/* ------------------------------------------------------------------ */

export interface TemplateDeckProps {
  open: boolean;
  templates: TemplateSummary[];
  activeCode: string | null;
  onSelect: (code: string) => void;
  onClose: () => void;
}

export function TemplateDeck({ open, templates, activeCode, onSelect, onClose }: TemplateDeckProps) {
  const [styleFilter, setStyleFilter] = React.useState<StyleFilter>('all');
  const [sceneFilter, setSceneFilter] = React.useState<SceneFilter>('all');
  const [layoutFilter, setLayoutFilter] = React.useState<LayoutFilter>('all');

  const [detailCode, setDetailCode] = React.useState<string | null>(null);
  const [detailRetry, setDetailRetry] = React.useState(0);
  const [detailState, setDetailState] = React.useState<DetailState>(EMPTY_DETAIL);

  /* 关闭时清掉详情状态，避免下次打开还挂着上次的请求结果 */
  React.useEffect(() => {
    if (!open) {
      setDetailCode(null);
      setDetailState(EMPTY_DETAIL);
    }
  }, [open]);

  /* 打开时锁 body 滚动，关闭 / 卸载时恢复原值（可能是用户自己设的样式） */
  React.useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  /* Esc：有详情层时先关详情，否则关闭整个画廊 */
  React.useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (detailCode) {
        setDetailCode(null);
        return;
      }
      onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, detailCode, onClose]);

  /* 详情层打开时才拉详情（loading / error / retry 都由这里驱动） */
  React.useEffect(() => {
    if (!open || !detailCode) return;
    let cancelled = false;
    setDetailState({ status: 'loading', detail: null, error: null });
    getTemplate(detailCode)
      .then((detail) => {
        if (!cancelled) setDetailState({ status: 'ready', detail, error: null });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setDetailState({ status: 'error', detail: null, error: describeError(error) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, detailCode, detailRetry]);

  const filtered = React.useMemo(() => {
    const sceneTag = SCENE_OPTIONS.find((option) => option.value === sceneFilter)?.tag ?? null;
    return templates.filter((template) => {
      if (styleFilter !== 'all' && resolveStyle(template) !== styleFilter) return false;
      if (layoutFilter !== 'all' && resolveLayout(template) !== layoutFilter) return false;
      if (sceneTag && !resolveTags(template).includes(sceneTag)) return false;
      return true;
    });
  }, [templates, styleFilter, sceneFilter, layoutFilter]);

  const resetFilters = React.useCallback(() => {
    setStyleFilter('all');
    setSceneFilter('all');
    setLayoutFilter('all');
  }, []);

  if (!open) return null;
  // 必须挂到 body：编辑器外层存在 transform / 动画容器，会在祖先上创建 containing block，
  // 让 `fixed inset-0` 退化成“相对父容器绝对定位”（画廊只占半屏、还压在 header 下面）。
  if (typeof document === 'undefined') return null;

  const activeDetailCode = detailCode;

  return createPortal(
    <>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="模板库"
        className="fixed inset-0 z-50 overflow-y-auto bg-white print:hidden"
      >
        {/* 顶部固定区：标题 + 数量 + 关闭；筛选紧随其后 */}
        <div className="sticky top-0 z-20 border-b border-ink-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h2 className="text-xl font-extrabold tracking-tightest text-ink-950 sm:text-2xl">
                模板库
              </h2>
              <p className="text-[12px] text-ink-500">
                {templates.length} 套版式
                {filtered.length !== templates.length ? ` · 当前显示 ${filtered.length} 套` : ''}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={onClose} aria-label="关闭模板库">
              <X className="h-3.5 w-3.5" aria-hidden />
              关闭
            </Button>
          </div>

          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 pb-4 sm:px-6">
            <div
              aria-label="按风格筛选模板"
              className="flex flex-wrap items-center gap-1"
            >
              {STYLE_TABS.map((option) => {
                const selected = styleFilter === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => setStyleFilter(option.value)}
                    className={cn(
                      'h-8 border px-3 text-[12px] font-semibold transition-colors duration-200 ease-snap focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-950',
                      selected
                        ? 'border-ink-950 bg-ink-950 text-white'
                        : 'border-ink-300 bg-white text-ink-600 hover:border-ink-950 hover:text-ink-950',
                    )}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>

            <div className="ml-auto flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-1.5 text-[11px] text-ink-500">
                <span>场景</span>
                <select
                  value={sceneFilter}
                  onChange={(event) => setSceneFilter(event.target.value as SceneFilter)}
                  className="h-8 border border-ink-300 bg-white px-2 text-[12px] text-ink-800 outline-none transition-colors hover:border-ink-950 focus:border-ink-950"
                >
                  {SCENE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-1.5 text-[11px] text-ink-500">
                <span>布局</span>
                <select
                  value={layoutFilter}
                  onChange={(event) => setLayoutFilter(event.target.value as LayoutFilter)}
                  className="h-8 border border-ink-300 bg-white px-2 text-[12px] text-ink-800 outline-none transition-colors hover:border-ink-950 focus:border-ink-950"
                >
                  {LAYOUT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </div>

        <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
          {templates.length === 0 ? (
            <EmptyState
              title="暂时没有可用模板"
              description="模板列表为空或后端暂不可达。可以直接关闭画廊，从空白内容手写。"
              action={
                <Button variant="outline" size="sm" onClick={onClose}>
                  返回编辑器
                </Button>
              }
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              title="没有符合条件的模板"
              description="当前筛选组合下没有模板，试试放宽风格 / 场景 / 布局，或直接重置筛选。"
              action={
                <Button variant="outline" size="sm" onClick={resetFilters}>
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                  重置筛选
                </Button>
              }
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
              {filtered.map((template) => (
                <TemplateCard
                  key={template.code}
                  template={template}
                  active={template.code === activeCode}
                  onOpen={() => setDetailCode(template.code)}
                  onSelect={() => onSelect(template.code)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {activeDetailCode ? (
        <TemplateDetailLayer
          state={detailState}
          activeCode={activeCode}
          onClose={() => setDetailCode(null)}
          onRetry={() => setDetailRetry((value) => value + 1)}
          onUse={(code) => {
            onSelect(code);
            setDetailCode(null);
          }}
        />
      ) : null}
    </>,
    document.body,
  );
}

export default TemplateDeck;