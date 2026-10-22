'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { useStepInput, type WheelMode } from './use-step-input';

/**
 * 竖向卡片滑块（分页切换）。
 *
 * 结构：外层是「可视窗口」（overflow: hidden），内部是一条 100% 高的 track，
 * 每张卡片用 `translateY(i * 100%)` 纵向排开；
 * 切换时只改 track 的 `translateY(-index * 100%)` —— 纯 2D 位移，
 * 没有 3D 翻转、没有旋转，每次只露出一张卡。
 *
 * 为什么用百分比：track 高度 = 视口高度，卡片高度 = 100%，所以
 * `translateY(-index * 100%)` 恰好等于 `-index × 单卡高度`，不用 JS 量尺寸，
 * 窗口缩放时自动跟着变。
 *
 * 防抖：动画期间输入严格丢弃（见 use-step-input.ts），动画结束才允许下一张。
 */
export interface VerticalCardSliderProps {
  items: React.ReactNode[];
  defaultIndex?: number;
  /** 单次滑动时长，ms，默认 620。 */
  duration?: number;
  /** 缓动，默认 cubic-bezier(0.22, 1, 0.36, 1)。 */
  easing?: string;
  /** 是否循环；默认 false（首尾截止，避免绕一圈的长距离回扫）。 */
  loop?: boolean;
  wheelMode?: WheelMode;
  keyboard?: boolean;
  showDots?: boolean;
  onChange?: (index: number) => void;
  onSettle?: (index: number) => void;
  ariaLabel?: string;
  className?: string;
  itemClassName?: string;
}

/** 平滑位移：Material 标准曲线，起步有加速、收尾缓出，全程都能看到滑动过程。 */
const EASING = 'cubic-bezier(0.4, 0, 0.2, 1)';

export function VerticalCardSlider({
  items,
  defaultIndex = 0,
  duration = 620,
  easing = EASING,
  loop = false,
  wheelMode = 'hover',
  keyboard = true,
  showDots = false,
  onChange,
  onSettle,
  ariaLabel = '卡片列表',
  className,
  itemClassName,
}: VerticalCardSliderProps) {
  const [announce, setAnnounce] = React.useState('');

  const slider = useStepInput({
    count: items.length,
    duration,
    defaultIndex,
    loop,
    wheelMode,
    keyboard,
    disabled: items.length === 0,
    onIndexChange: (next) => onChange?.(next),
    onSettle: (settled) => {
      setAnnounce(`第 ${settled + 1} 张，共 ${items.length} 张`);
      onSettle?.(settled);
    },
  });

  // 非当前卡片整块 aria-hidden + inert，避免读屏/Tab 进入看不见的卡。
  const cardRefs = React.useRef<Array<HTMLDivElement | null>>([]);
  React.useEffect(() => {
    cardRefs.current.forEach((el, i) => {
      if (!el) return;
      const hidden = i !== slider.index;
      el.setAttribute('aria-hidden', hidden ? 'true' : 'false');
      (el as HTMLDivElement & { inert?: boolean }).inert = hidden;
    });
  }, [slider.index]);

  if (items.length === 0) return null;

  return (
    <div
      ref={slider.containerRef}
      role="region"
      aria-roledescription="卡片列表"
      aria-label={ariaLabel}
      tabIndex={0}
      data-slider-index={slider.index}
      className={cn(
        'relative overflow-hidden outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white/70',
        className,
      )}
    >
      <div
        data-slider-track=""
        className="absolute inset-0 will-change-transform"
        style={{
          transform: `translateY(${-slider.index * 100}%)`,
          transition: `transform ${duration}ms ${easing}`,
        }}
        onTransitionEnd={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.propertyName !== 'transform') return;
          slider.settle();
        }}
      >
        {items.map((item, i) => (
          <div
            key={i}
            ref={(el) => {
              cardRefs.current[i] = el;
            }}
            className={cn('absolute inset-x-0 top-0 h-full', itemClassName)}
            style={{ transform: `translateY(${i * 100}%)` }}
            aria-hidden={i !== slider.index}
          >
            {item}
          </div>
        ))}
      </div>

      {showDots ? (
        <div className="absolute inset-x-0 bottom-3 z-10 flex items-center justify-center gap-1.5">
          {items.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => slider.goTo(i)}
              aria-label={`查看第 ${i + 1} 张，共 ${items.length} 张`}
              aria-current={i === slider.index ? 'true' : undefined}
              className="flex h-6 w-6 items-center justify-center"
            >
              <span
                className={cn(
                  'h-1 rounded-full transition-all duration-300',
                  i === slider.index ? 'w-5 bg-white' : 'w-1.5 bg-white/30 hover:bg-white/50',
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

export default VerticalCardSlider;
