'use client';

import * as React from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStepInput } from '@/components/vertical-card-slider/use-step-input';

/**
 * 整页分页容器（fullpage 式）。
 *
 * 行为：
 * - 每一屏是一张「整页幻灯片」，外层窗口 overflow:hidden，内部 track 用 translateY 位移；
 * - 滚轮 / ↑↓ 一次翻一屏，**严格防抖**：动画结束前输入直接丢弃；
 * - 站点 header 保留在顶部（62px），deck 从 header 下方铺到视口底部，所以页面没有原生滚动条；
 * - 单屏内容超出高度时，滚轮优先滚动屏内区域（[data-deck-scroll]），到边缘后才翻页。
 *
 * 为什么不用原生 scroll-snap：需求是「一次手势 = 一屏、动画期间禁止重复触发」，
 * 原生滚动无法做到严格防抖，且会带出原生滚动条。
 */
export interface FullPageDeckProps {
  slides: React.ReactNode[];
  /** 每屏的短标题（用于顶栏与进度点 aria-label）。 */
  slideLabels?: string[];
  /** 顶栏左侧的上位标签，如「单份诊断」「HR 批量初筛」。 */
  eyebrow?: string;
  index?: number;
  defaultIndex?: number;
  onIndexChange?: (index: number) => void;
  duration?: number;
  ariaLabel?: string;
  className?: string;
}

const SITE_HEADER_HEIGHT = 63; // h-[62px] + 1px 下边框，与 components/site-header 对齐
const EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

function pad(value: number) {
  return String(value).padStart(2, '0');
}

export function FullPageDeck({
  slides,
  slideLabels,
  eyebrow,
  index,
  defaultIndex = 0,
  onIndexChange,
  duration = 620,
  ariaLabel = '分页内容',
  className,
}: FullPageDeckProps) {
  const count = Math.max(1, slides.length);
  const labels = React.useMemo(
    () => slideLabels ?? slides.map((_, i) => `第 ${i + 1} 页`),
    [slideLabels, slides],
  );

  const [announce, setAnnounce] = React.useState('');

  const deck = useStepInput({
    count,
    duration,
    defaultIndex,
    wheelMode: 'hover',
    keyboard: true,
    disabled: slides.length === 0,
    onIndexChange: (next) => onIndexChange?.(next),
    onSettle: (settled) => setAnnounce(`第 ${settled + 1} 页，共 ${count} 页：${labels[settled] ?? ''}`),
    // 指针在「屏内可滚动区域」且该区域还没滚到边缘时，把滚轮让给原生滚动
    wheelGuard: (event) => {
      const target = event.target as HTMLElement | null;
      const scroller = target?.closest('[data-deck-scroll]') as HTMLElement | null;
      if (!scroller) return false;
      const canScrollDown = scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1;
      const canScrollUp = scroller.scrollTop > 1;
      return event.deltaY > 0 ? canScrollDown : canScrollUp;
    },
  });

  // 受控：外部 index 变化时跳过去（自动推进到结果页就靠它）
  const goToRef = React.useRef(deck.goTo);
  goToRef.current = deck.goTo;
  const currentRef = React.useRef(deck.index);
  currentRef.current = deck.index;
  React.useEffect(() => {
    if (index == null) return;
    if (index !== currentRef.current) goToRef.current(index);
  }, [index]);

  // 分页页面禁用页面级滚动：没有原生滚动条
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = 'hidden';
    body.style.overflow = 'hidden';
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, []);

  // 让键盘可以直接翻页；不抢输入框的焦点
  React.useEffect(() => {
    const el = deck.containerRef.current;
    if (!el) return;
    const active = document.activeElement;
    if (!active || active === document.body) el.focus({ preventScroll: true });
  }, [deck.containerRef]);

  // 非当前屏整块 aria-hidden + inert，避免 Tab/读屏进入看不见的内容
  const slideRefs = React.useRef<Array<HTMLElement | null>>([]);
  React.useEffect(() => {
    slideRefs.current.forEach((el, i) => {
      if (!el) return;
      const hidden = i !== deck.index;
      el.setAttribute('aria-hidden', hidden ? 'true' : 'false');
      (el as HTMLElement & { inert?: boolean }).inert = hidden;
    });
  }, [deck.index]);

  return (
    <div
      ref={deck.containerRef}
      role="region"
      aria-roledescription="分页容器"
      aria-label={ariaLabel}
      tabIndex={0}
      data-deck-index={deck.index}
      className={cn(
        'fixed inset-x-0 bottom-0 z-30 flex flex-col bg-ink-50 outline-none',
        className,
      )}
      style={{ top: SITE_HEADER_HEIGHT }}
    >
      {/* 顶栏：当前环节（背景通栏，内容与页面内容列对齐） */}
      <div className="shrink-0 border-b border-ink-200 bg-white">
        <div className="mx-auto flex h-14 w-full max-w-[1120px] items-center justify-between gap-4 px-5 sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            {eyebrow ? (
              <span className="shrink-0 text-[10.5px] font-bold uppercase tracking-[0.18em] text-ink-500">
                {eyebrow}
              </span>
            ) : null}
            <span aria-hidden className="h-3 w-px shrink-0 bg-ink-300" />
            <span className="truncate text-[12px] font-semibold tracking-tight text-ink-950">
              {labels[deck.index] ?? ''}
            </span>
          </div>
          <span className="tnum shrink-0 text-[12px] font-semibold text-ink-950">
            {pad(deck.index + 1)} <span className="font-medium text-ink-400">/ {pad(count)}</span>
          </span>
        </div>
      </div>

      {/* 视口：一次只露一屏 */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div
          data-deck-track=""
          className="absolute inset-0 will-change-transform"
          style={{
            transform: `translateY(${-deck.index * 100}%)`,
            transition: `transform ${duration}ms ${EASING}`,
          }}
          onTransitionEnd={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.propertyName !== 'transform') return;
            deck.settle();
          }}
        >
          {slides.map((slide, i) => (
            <section
              key={i}
              ref={(el) => {
                slideRefs.current[i] = el;
              }}
              className="absolute inset-x-0 top-0 h-full"
              style={{ transform: `translateY(${i * 100}%)` }}
              aria-hidden={i !== deck.index}
            >
              <div data-deck-scroll="" className="h-full overflow-y-auto overscroll-contain">
                <div className="mx-auto flex min-h-full w-full max-w-[1120px] flex-col px-5 py-8 sm:px-8">
                  <div className="my-auto w-full">{slide}</div>
                </div>
              </div>
            </section>
          ))}
        </div>
      </div>

      {/* 底栏：提示 + 进度点 + 上一页/下一页（背景通栏，内容与页面内容列对齐） */}
      <div className="shrink-0 border-t border-ink-200 bg-white">
        <div className="mx-auto flex h-16 w-full max-w-[1120px] items-center justify-between gap-4 px-5 sm:px-8">
          <div className="hidden items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-ink-500 sm:flex">
            <span className="border border-ink-300 px-1.5 py-0.5 text-ink-700">滚轮</span>
            <span className="text-ink-300">/</span>
            <span className="border border-ink-300 px-1.5 py-0.5 text-ink-700">↑ ↓</span>
            <span>翻页</span>
          </div>

          <div className="flex flex-1 items-center justify-center gap-1 sm:flex-none">
            {slides.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => deck.goTo(i)}
                aria-label={`第 ${i + 1} 页${labels[i] ? `：${labels[i]}` : ''}`}
                aria-current={i === deck.index ? 'true' : undefined}
                className="group flex h-8 items-center px-1.5"
              >
                <span
                  className={cn(
                    'h-[3px] transition-all duration-300',
                    i === deck.index ? 'w-7 bg-ink-950' : 'w-3 bg-ink-300 group-hover:bg-ink-400',
                  )}
                />
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={deck.prev}
              disabled={deck.index === 0}
              aria-label="上一页"
              className="flex h-8 w-8 items-center justify-center border border-ink-300 text-ink-600 transition-colors hover:border-ink-950 hover:text-ink-950 disabled:pointer-events-none disabled:opacity-30"
            >
              <ArrowUp className="h-4 w-4" aria-hidden />
            </button>
            <button
              type="button"
              onClick={deck.next}
              disabled={deck.index === count - 1}
              aria-label="下一页"
              className="flex h-8 w-8 items-center justify-center border border-ink-300 text-ink-600 transition-colors hover:border-ink-950 hover:text-ink-950 disabled:pointer-events-none disabled:opacity-30"
            >
              <ArrowDown className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </div>
      </div>

      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announce}
      </p>
    </div>
  );
}

export default FullPageDeck;
