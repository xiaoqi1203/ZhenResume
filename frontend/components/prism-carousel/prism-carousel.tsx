'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { usePagedInput, type WheelMode } from './use-paged-input';

/**
 * 旋转到任意角度时，卡柱在「垂直于旋转轴」方向上的最大投影尺寸。
 * 绕 X 轴翻页时卡片会纵向展开到约 2R（R = 外接半径），舞台高度必须按它预留，
 * 否则翻到 45° 的卡片会压到下面的提示文案上。
 */
function projectedAxisExtent(axisLength: number, faces: number, perspective: number): number {
  const half = axisLength / 2;
  const apothem = half / Math.tan(Math.PI / faces);
  const circumradius = half / Math.sin(Math.PI / faces);
  let max = 0;
  for (let deg = 0; deg <= 180; deg += 1) {
    const rad = (deg * Math.PI) / 180;
    const projected =
      (circumradius * Math.sin(rad) * perspective) /
      (perspective + apothem - circumradius * Math.cos(rad));
    if (projected > max) max = projected;
  }
  return max * 2;
}

/**
 * 3D 多面棱柱卡片（分页翻转）。
 *
 * 两种旋转轴，几何同一套公式、只是「边长」取哪个维度不同：
 *   step = 360° / N
 *   r    = (edge / 2) / tan(π / N)      // 卡面中心到转轴的距离
 *   面 i ： rotateX/Y(i · step) translateZ(r)
 *   容器 ： translateZ(-r) rotateX/Y(-virtual · step)
 *
 * - axis = 'x'（默认）：绕水平轴上下翻页，edge = 卡高；
 * - axis = 'y'：绕竖直轴左右翻页，edge = 卡宽。
 *
 * 关键点：容器先 translateZ(-r) 再旋转，让**正面卡片精确落在 z=0 平面**——
 * 透视对它零形变，尺寸严格 1:1、文字不放大；转轴仍然穿过柱体中心。
 *
 * 交互：滚轮一格 / ↓ 翻下一面，滚轮反向 / ↑ 翻上一面；不是随滚动进度旋转，
 * 而是「一次手势 → 一步 → 固定时长过渡」。详见 use-paged-input.ts。
 */
export interface PrismCarouselProps {
  /** N 个面，每个元素就是一张卡的内容。 */
  items: React.ReactNode[];
  defaultIndex?: number;
  /** 受控索引（可选）。 */
  index?: number;
  onIndexChange?: (index: number) => void;
  /** 是否循环，默认 true。 */
  loop?: boolean;
  /** 滚轮作用域，默认 'hover'。 */
  wheelMode?: WheelMode;
  /** 连续滚动（触控板）翻页阈值，px，默认 60。 */
  wheelThreshold?: number;
  /** 锁内缓冲步数，默认 1。 */
  queueDepth?: number;
  /** 单步动画时长，ms，默认 620。 */
  duration?: number;
  /** 透视距离，px，默认 1500（≈4.4 × 卡宽）。 */
  perspective?: number;
  /** 旋转轴：'x' = 上下翻页（垂直，默认）；'y' = 左右翻页（水平）。 */
  axis?: 'x' | 'y';
  cardWidth?: number;
  cardHeight?: number;
  /** 卡片圆角，px，默认 24。 */
  radius?: number;
  shadow?: boolean;
  showDots?: boolean;
  showHint?: boolean;
  ariaLabel?: string;
  className?: string;
  cardClassName?: string;
}

/**
 * 620ms 走完 90°。缓动用 Material 标准曲线：起步有短暂加速、收尾匀速淡出，
 * 比 ease-out-quint 更"看得见过程"——quint 会在前 1/3 时间跑完 87% 角度，
 * 剩下 400ms 只挪 12°，观感上像瞬间切换 + 拖尾。
 */
const EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';
const SHADOW =
  '0 36px 72px -34px rgba(17, 24, 39, 0.45), 0 12px 26px -18px rgba(17, 24, 39, 0.28)';
/** 相邻面最大压暗强度（正面 0 → 侧面 ~0.17 → 背面 0.3，背面本身被 backface 隐藏）。 */
const MAX_SHADE = 0.3;

export function PrismCarousel({
  items,
  defaultIndex = 0,
  index,
  onIndexChange,
  loop = true,
  wheelMode = 'hover',
  wheelThreshold = 60,
  queueDepth = 1,
  duration = 620,
  perspective = 1500,
  axis = 'x',
  cardWidth = 340,
  cardHeight = 452,
  radius = 24,
  shadow = true,
  showDots = true,
  showHint = true,
  ariaLabel = '棱柱卡片轮播',
  className,
  cardClassName,
}: PrismCarouselProps) {
  const count = Math.max(1, items.length);
  const stepDeg = 360 / count;

  const [reducedMotion, setReducedMotion] = React.useState(false);
  React.useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);
  const effectiveDuration = reducedMotion ? Math.min(160, duration) : duration;

  // 卡片的实际像素尺寸由 CSS（min(340px, 74vw) + aspect-ratio）决定，这里量出来再算几何。
  const rotorRef = React.useRef<HTMLDivElement>(null);
  const [size, setSize] = React.useState({ w: cardWidth, h: cardHeight });
  const [measured, setMeasured] = React.useState(false);
  React.useEffect(() => {
    const el = rotorRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box || box.width <= 0 || box.height <= 0) return;
      const w = Math.round(box.width * 100) / 100;
      const h = Math.round(box.height * 100) / 100;
      setSize((prev) => (Math.abs(prev.w - w) < 0.5 && Math.abs(prev.h - h) < 0.5 ? prev : { w, h }));
      setMeasured(true);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const [announce, setAnnounce] = React.useState('');

  const paging = usePagedInput({
    count,
    duration: effectiveDuration,
    defaultIndex,
    loop,
    wheelMode,
    threshold: wheelThreshold,
    queueDepth,
    disabled: items.length === 0,
    onIndexChange: (next) => onIndexChange?.(next),
    onSettle: (settled) => setAnnounce(`第 ${settled + 1} 张，共 ${count} 张`),
  });
  const { index: activeIndex, virtual, moveDuration } = paging;

  // 受控模式：外部 index 变化时走最短路径跳转（内部跳转不会再触发一次）。
  const jumpRef = React.useRef(paging.jumpTo);
  jumpRef.current = paging.jumpTo;
  const indexRef = React.useRef(activeIndex);
  indexRef.current = activeIndex;
  React.useEffect(() => {
    if (index == null) return;
    const target = ((index % count) + count) % count;
    if (target !== indexRef.current) jumpRef.current(target);
  }, [index, count]);

  // 只有正面卡进入可达性树与 Tab 序列；其余面 aria-hidden + inert。
  const faceRefs = React.useRef<Array<HTMLDivElement | null>>([]);
  React.useEffect(() => {
    faceRefs.current.forEach((el, i) => {
      if (!el) return;
      el.setAttribute('aria-hidden', i === activeIndex ? 'false' : 'true');
      (el as HTMLDivElement & { inert?: boolean }).inert = i !== activeIndex;
    });
  }, [activeIndex]);

  const hintId = React.useId();
  // 绕 X 轴时棱柱的“边长”是卡高，绕 Y 轴时是卡宽；半径只由边长决定。
  const axisLength = axis === 'x' ? size.h : size.w;
  const rotorRadius = axisLength / 2 / Math.tan(Math.PI / count);
  const stageHeight =
    axis === 'x'
      ? Math.round(Math.max(size.h, projectedAxisExtent(size.h, count, perspective)) + 64)
      : Math.round(size.h * 1.08 + 72);
  const rotateFn = axis === 'x' ? 'rotateX' : 'rotateY';
  // 方向：X 轴时让「下一面」从**下方**翻上来（内容向上走，符合向下滚的自然方向）；
  // Y 轴时「下一面」从右方进入。两个符号必须相反，否则面序和旋转方向会对不上。
  const faceSign = axis === 'x' ? -1 : 1;

  const rotorStyle: React.CSSProperties = {
    width: `min(${cardWidth}px, 74vw)`,
    aspectRatio: `${cardWidth} / ${cardHeight}`,
    transform: `translateZ(${-rotorRadius}px) ${rotateFn}(${-faceSign * virtual * stepDeg}deg)`,
    transformStyle: 'preserve-3d',
    willChange: 'transform',
    transition: measured ? `transform ${moveDuration}ms ${EASING}` : 'none',
  };

  const shadeTransition = measured ? `background-color ${moveDuration}ms ${EASING}` : 'none';

  const faces = items.map((item, i) => {
    const raw = (i - virtual) * stepDeg;
    const relative = (((raw % 360) + 540) % 360) - 180; // -180..180
    const shade = Math.min(MAX_SHADE, (Math.abs(relative) / 180) * MAX_SHADE * 1.15);
    const isFront = i === activeIndex;

    return (
      <div
        key={i}
        ref={(el) => {
          faceRefs.current[i] = el;
        }}
        className="absolute inset-0"
        style={{
          transform: `${rotateFn}(${faceSign * i * stepDeg}deg) translateZ(${rotorRadius}px)`,
          backfaceVisibility: 'hidden',
          WebkitBackfaceVisibility: 'hidden',
        }}
        aria-hidden={!isFront}
      >
        <div
          className={cn(
            'absolute inset-0 overflow-hidden border border-ink-200 bg-white text-ink-900',
            cardClassName,
          )}
          style={{ borderRadius: radius, boxShadow: shadow ? SHADOW : 'none' }}
        >
          {item}
        </div>
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            borderRadius: radius,
            backgroundColor: `rgba(12, 12, 20, ${shade.toFixed(3)})`,
            transition: shadeTransition,
          }}
        />
      </div>
    );
  });

  if (items.length === 0) return null;

  return (
    <div
      ref={paging.containerRef}
      role="region"
      aria-roledescription="3D 卡片轮播"
      aria-label={ariaLabel}
      aria-describedby={showHint ? hintId : undefined}
      tabIndex={0}
      data-prism-index={activeIndex}
      data-prism-virtual={virtual}
      className={cn('group/prism relative flex flex-col items-center gap-7 outline-none', className)}
    >
      <div
        className="relative flex w-full items-center justify-center"
        style={{
          perspective: `${perspective}px`,
          perspectiveOrigin: '50% 50%',
          height: stageHeight,
        }}
      >
        <div ref={rotorRef} data-prism-rotor="" className="relative" style={rotorStyle}>
          {faces}
        </div>
      </div>

      {showHint ? (
        <p
          id={hintId}
          className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-500"
        >
          <span className="border border-ink-300 px-1.5 py-0.5 text-ink-700">滚轮</span>
          <span className="text-ink-300">/</span>
          <span className="border border-ink-300 px-1.5 py-0.5 text-ink-700">↑ ↓</span>
          <span>切换卡片</span>
        </p>
      ) : null}

      {showDots ? (
        <div className="flex items-center justify-center gap-1">
          {items.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => paging.jumpTo(i)}
              aria-label={`查看第 ${i + 1} 张，共 ${count} 张`}
              aria-current={i === activeIndex ? 'true' : undefined}
              className="flex h-8 w-8 items-center justify-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
            >
              <span
                className={cn(
                  'h-1.5 rounded-full transition-all duration-200',
                  i === activeIndex
                    ? 'w-6 bg-ink-950'
                    : 'w-3 bg-ink-300 group-hover/prism:bg-ink-400',
                )}
              />
            </button>
          ))}
        </div>
      ) : null}

      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announce}
      </p>
    </div>
  );
}

export default PrismCarousel;
