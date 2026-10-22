'use client';

import * as React from 'react';

/**
 * 分页步进输入（滚轮 / 键盘），用于「一次手势 = 切换一张卡片」的 translateY 滑块。
 *
 * 与 3D 棱柱版的区别：这里要求**严格防抖** ——
 * 动画进行中收到的任何输入都直接丢弃，不缓冲、不排队；
 * 必须等 transition 结束（且触控板惯性静默）后，下一次手势才会生效。
 */

export type StepDirection = 1 | -1;
export type WheelMode = 'hover' | 'focus' | 'off';

export interface StepInputOptions {
  count: number;
  /** 单次滑动时长（ms），锁会等它再加 120ms 兜底。 */
  duration: number;
  defaultIndex?: number;
  loop?: boolean;
  wheelMode?: WheelMode;
  keyboard?: boolean;
  /** 触控板连续滚动累积阈值（px）。 */
  threshold?: number;
  /** 鼠标滚轮「一格」判定阈值（px）；deltaMode ≠ 0 一律算一格。 */
  notchThreshold?: number;
  /** 动画结束后的惯性静默期（ms）。 */
  silence?: number;
  disabled?: boolean;
  /**
   * 滚轮拦截守卫：返回 true 表示这一下滚轮不归分页管（例如指针在内部可滚动区域里），
   * 交给浏览器原生滚动，也不调用 preventDefault。
   */
  wheelGuard?: (event: WheelEvent) => boolean;
  onIndexChange?: (index: number, direction: StepDirection) => void;
  onSettle?: (index: number) => void;
}

export interface StepInputApi {
  index: number;
  animating: boolean;
  containerRef: React.RefObject<HTMLDivElement>;
  /** transitionend 时调用（幂等）。 */
  settle: () => void;
  goTo: (index: number) => void;
  next: () => void;
  prev: () => void;
}

const mod = (value: number, size: number) => ((value % size) + size) % size;

export function useStepInput(options: StepInputOptions): StepInputApi {
  const count = Math.max(1, options.count);
  const duration = Math.max(80, options.duration);

  const optionsRef = React.useRef(options);
  optionsRef.current = options;

  const [index, setIndex] = React.useState(() => {
    const raw = options.defaultIndex ?? 0;
    return options.loop ? mod(raw, count) : Math.min(count - 1, Math.max(0, raw));
  });
  const [animating, setAnimating] = React.useState(false);

  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const indexRef = React.useRef(index);
  const animatingRef = React.useRef(false);
  /** 输入锁：动画中 + 惯性静默期内都为 true。 */
  const lockedRef = React.useRef(false);
  /** 动画期间是否还有滚轮事件（用来判断要不要等惯性静默）。 */
  const wheelDuringAnimRef = React.useRef(false);
  const tokenRef = React.useRef(0);
  const accRef = React.useRef(0);
  const lastWheelRef = React.useRef(0);
  const settleTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const unlockTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = React.useRef(true);

  const clampTarget = (raw: number) => {
    const total = Math.max(1, optionsRef.current.count);
    return optionsRef.current.loop ? mod(raw, total) : Math.max(0, Math.min(total - 1, raw));
  };

  function releaseWhenSilent() {
    const o = optionsRef.current;
    const wait = lastWheelRef.current + (o.silence ?? 160) - Date.now();
    if (wait <= 0) {
      lockedRef.current = false;
      return;
    }
    if (unlockTimerRef.current) clearTimeout(unlockTimerRef.current);
    unlockTimerRef.current = setTimeout(() => {
      unlockTimerRef.current = null;
      if (mountedRef.current && !animatingRef.current) releaseWhenSilent();
    }, wait + 16);
  }

  function finish(token: number) {
    if (token !== tokenRef.current || !animatingRef.current) return;
    animatingRef.current = false;
    setAnimating(false);
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    optionsRef.current.onSettle?.(indexRef.current);
    // 动画期间没有新的滚轮输入 → 立刻解锁；有 → 等惯性尾巴安静下来再解锁，
    // 否则触控板一次性甩动的惯性会在解锁瞬间再翻一张。
    if (wheelDuringAnimRef.current) releaseWhenSilent();
    else lockedRef.current = false;
  }

  /** 开始一段位移；返回是否真的切了。被锁时直接拒绝（严格防抖）。 */
  function moveTo(target: number, dir: StepDirection) {
    if (lockedRef.current) return false;
    const next = clampTarget(target);
    if (next === indexRef.current) return false;

    const o = optionsRef.current;
    const token = ++tokenRef.current;
    lockedRef.current = true;
    animatingRef.current = true;
    wheelDuringAnimRef.current = false;
    accRef.current = 0;
    setAnimating(true);
    indexRef.current = next;
    setIndex(next);
    o.onIndexChange?.(next, dir);

    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
    settleTimerRef.current = setTimeout(() => finish(token), duration + 120);
    return true;
  }

  const step = (dir: StepDirection) => {
    if (lockedRef.current) return;
    moveTo(indexRef.current + dir, dir);
  };

  const goTo = (target: number) => {
    if (lockedRef.current) return;
    const to = clampTarget(target);
    if (to === indexRef.current) return;
    moveTo(to, to > indexRef.current ? 1 : -1);
  };

  const handleWheel = (event: WheelEvent) => {
    const o = optionsRef.current;
    if (o.disabled || o.wheelMode === 'off') return;
    if (event.ctrlKey) return; // 触控板捏合缩放
    const dy = event.deltaY;
    if (!dy) return;
    if (Math.abs(event.deltaX) > Math.abs(dy)) return;
    if (o.wheelMode === 'focus') {
      const el = containerRef.current;
      const active = typeof document === 'undefined' ? null : document.activeElement;
      if (!el || !active || !el.contains(active)) return;
    }

    const dir: StepDirection = dy > 0 ? 1 : -1;

    // 指针落在内部可滚动区域（时间线、表格、长文本）里时，把滚轮让给它。
    if (o.wheelGuard?.(event)) return;

    if (lockedRef.current) {
      // 严格防抖：动画期间不消费、不累积，只记录「还有滚动输入」用于惯性判断。
      wheelDuringAnimRef.current = true;
      lastWheelRef.current = Date.now();
      accRef.current = 0;
      event.preventDefault();
      return;
    }

    const target = clampTarget(indexRef.current + dir);
    if (target === indexRef.current) return; // 到首/尾且不循环：把滚轮还给页面

    event.preventDefault();
    const discrete = event.deltaMode !== 0 || Math.abs(dy) >= (o.notchThreshold ?? 60);
    lastWheelRef.current = Date.now();

    if (discrete) {
      accRef.current = 0;
      step(dir);
      return;
    }
    accRef.current += dy;
    if (Math.abs(accRef.current) >= (o.threshold ?? 42)) {
      accRef.current = 0;
      step(dir);
    }
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    const o = optionsRef.current;
    if (o.disabled || o.keyboard === false) return;
    const target = event.target as HTMLElement | null;
    if (target) {
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return;
    }
    switch (event.key) {
      case 'ArrowDown':
      case 'PageDown':
        event.preventDefault();
        step(1);
        return;
      case 'ArrowUp':
      case 'PageUp':
        event.preventDefault();
        step(-1);
        return;
      case 'Home':
        event.preventDefault();
        goTo(0);
        return;
      case 'End':
        event.preventDefault();
        goTo(Math.max(0, o.count - 1));
        return;
      default:
        return;
    }
  };

  const wheelRef = React.useRef(handleWheel);
  wheelRef.current = handleWheel;
  const keyRef = React.useRef(handleKeyDown);
  keyRef.current = handleKeyDown;

  React.useEffect(() => {
    mountedRef.current = true;
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => wheelRef.current(event);
    const onKey = (event: KeyboardEvent) => keyRef.current(event);
    // 必须显式 passive:false，否则 preventDefault 无法阻止页面滚动
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('keydown', onKey);
    return () => {
      mountedRef.current = false;
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('keydown', onKey);
      if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
      if (unlockTimerRef.current) clearTimeout(unlockTimerRef.current);
      settleTimerRef.current = null;
      unlockTimerRef.current = null;
      lockedRef.current = false;
      animatingRef.current = false;
    };
  }, []);

  return {
    index,
    animating,
    containerRef,
    settle: () => finish(tokenRef.current),
    goTo,
    next: () => step(1),
    prev: () => step(-1),
  };
}
