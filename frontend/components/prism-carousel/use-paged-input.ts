'use client';

import * as React from 'react';

/**
 * 分页式输入状态机 —— 3D 棱柱卡片的「一次手势 = 翻一面」逻辑。
 *
 * 设计要点（与滚动联动方案的本质区别）：
 * 1. **与滚动进度完全解耦**：不监听 scroll、不做插值，只消费离散手势，步进后交给 CSS transition。
 * 2. **滚轮归一化**：deltaMode 1/2（行/页）直接视为「一格」；px 模式下 |deltaY| ≥ 80 也算「一格」。
 *    触控板是高频小 delta 流，走累积阈值（默认 60px）。
 * 3. **锁 + 缓冲**：动画期间锁住输入；离散的一格最多缓冲 1 步，动画结束后立刻接上（跟手）。
 *    惯性/触控板连续 delta 直接丢弃，绝不在锁结束后补跳。
 * 4. **惯性静默期**：解锁条件 = 动画结束 **且** 距最近一次 wheel 事件 > 200ms，
 *    这样 macOS 触控板 1~2 秒的惯性尾巴不会在解锁瞬间再翻一页。
 * 5. **虚拟索引无界**：角度只增不减，显示时取模。CSS 里相差 360° 整数倍的旋转完全等价，
 *    因此永远不需要「瞬时归零」这种会产生闪帧的补丁。
 */

export type StepDirection = 1 | -1;

/** 滚轮作用域：悬停即拦截 / 仅聚焦时拦截 / 关闭。 */
export type WheelMode = 'hover' | 'focus' | 'off';

export interface PagedInputOptions {
  /** 面数（= items.length）。 */
  count: number;
  /** 单步动画时长（ms）。锁会在此时长后再加 90ms 兜底。 */
  duration: number;
  defaultIndex?: number;
  /** 是否循环；false 时首尾截止，并把滚轮交还给页面滚动。 */
  loop?: boolean;
  wheelMode?: WheelMode;
  keyboard?: boolean;
  /** 连续滚动翻页阈值（px）。 */
  threshold?: number;
  /** 离散滚轮判定阈值（px）；deltaMode ≠ 0 一律视为离散。 */
  notchThreshold?: number;
  /** 锁内最多缓冲几步（0 = 不缓冲）。 */
  queueDepth?: number;
  /** 「新手势」判定间隔（ms）：两次离散事件间隔大于它才算新一次拨动。 */
  gestureGap?: number;
  /** 惯性静默期（ms）。 */
  silence?: number;
  disabled?: boolean;
  onIndexChange?: (index: number, direction: StepDirection) => void;
  /** 动画真正停稳后触发（用于 aria-live 播报）。 */
  onSettle?: (index: number) => void;
}

export interface PagedInputApi {
  /** 无界虚拟索引，用于旋转角度（避免归零瞬移）。 */
  virtual: number;
  /** 当前正面索引 0..count-1。 */
  index: number;
  /** 最近一次步进方向。 */
  direction: StepDirection;
  /** 当前这一步的动画时长（多步跳转时会拉长，供 CSS transition 对齐）。 */
  moveDuration: number;
  containerRef: React.RefObject<HTMLDivElement>;
  /** 由 transitionend 调用；幂等，可安全重复调用。 */
  settle: () => void;
  /** 跳到指定面（走最短路径；非循环时走直线）。 */
  jumpTo: (index: number) => void;
  next: () => void;
  prev: () => void;
}

const mod = (value: number, size: number) => ((value % size) + size) % size;

export function usePagedInput(options: PagedInputOptions): PagedInputApi {
  const count = Math.max(1, options.count);
  const duration = Math.max(80, options.duration);

  // 事件监听只挂载一次，所有回调都通过 optionsRef 读取最新 props，避免闭包陈旧。
  const optionsRef = React.useRef(options);
  optionsRef.current = options;

  const [virtual, setVirtual] = React.useState(() => {
    const raw = options.defaultIndex ?? 0;
    return options.loop === false ? Math.min(count - 1, Math.max(0, raw)) : raw;
  });
  const [direction, setDirection] = React.useState<StepDirection>(1);
  const [moveDuration, setMoveDuration] = React.useState(duration);

  const containerRef = React.useRef<HTMLDivElement | null>(null);

  const virtualRef = React.useRef(virtual);
  const lockedRef = React.useRef(false);
  const animatingRef = React.useRef(false);
  const tokenRef = React.useRef(0);
  const queueRef = React.useRef<StepDirection[]>([]);
  const accRef = React.useRef(0);
  /** 最近一次 wheel 事件时间戳；0 表示很久没有滚轮输入（静默条件天然满足）。 */
  const lastWheelRef = React.useRef(0);
  /**
   * 上一次 wheel 事件是否是「连续小 delta」（触控板/惯性）。
   * 锁内缓冲只接受「上一次也是离散一格」或「间隔足够久」的离散事件，
   * 这样快速连拨鼠标（\~60ms 一格）能进缓冲，而惯性尾巴里偶尔的大 delta 不会被误判成新的一次拨动。
   */
  const lastContinuousRef = React.useRef(false);
  const settleTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const unlockTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = React.useRef(true);

  const currentCount = () => Math.max(1, optionsRef.current.count);
  const currentIndex = () => mod(virtualRef.current, currentCount());

  const canMove = (dir: StepDirection) => {
    const o = optionsRef.current;
    if (o.loop !== false) return true;
    const next = virtualRef.current + dir;
    return next >= 0 && next < Math.max(1, o.count);
  };

  const queueStep = (dir: StepDirection) => {
    const o = optionsRef.current;
    const depth = Math.max(0, o.queueDepth ?? 1);
    if (depth === 0) return;
    if (queueRef.current.length >= depth) return;
    if (o.loop === false) {
      const next = virtualRef.current + queueRef.current.length * dir + dir;
      if (next < 0 || next >= Math.max(1, o.count)) return;
    }
    queueRef.current.push(dir);
  };

  /** 开始一段旋转：更新虚拟索引 + 上锁 + 兜底计时器。 */
  function commit(nextVirtual: number, dir: StepDirection, ms: number) {
    const o = optionsRef.current;
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    const token = ++tokenRef.current;
    animatingRef.current = true;
    lockedRef.current = true;
    virtualRef.current = nextVirtual;
    setVirtual(nextVirtual);
    setDirection(dir);
    setMoveDuration(ms);
    o.onIndexChange?.(mod(nextVirtual, Math.max(1, o.count)), dir);
    settleTimerRef.current = setTimeout(() => finish(token), ms + 90);
  }

  /** 动画结束（transitionend 或兜底计时器）：先消费缓冲，再等惯性静默后解锁。 */
  function finish(token: number) {
    if (token !== tokenRef.current || !animatingRef.current) return;
    animatingRef.current = false;
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    const o = optionsRef.current;
    const queued = queueRef.current.shift();
    if (queued) {
      const next = virtualRef.current + queued;
      const reachable = o.loop !== false || (next >= 0 && next < Math.max(1, o.count));
      if (reachable) {
        commit(next, queued, o.duration);
        return;
      }
    }
    o.onSettle?.(mod(virtualRef.current, Math.max(1, o.count)));
    releaseWhenSilent();
  }

  function releaseWhenSilent() {
    const o = optionsRef.current;
    const silence = Math.max(0, o.silence ?? 200);
    const wait = lastWheelRef.current + silence - Date.now();
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

  /** 走一步；被锁时转入缓冲（最多 queueDepth 步）。 */
  const step = (dir: StepDirection) => {
    if (!canMove(dir)) return;
    if (lockedRef.current) {
      queueStep(dir);
      return;
    }
    commit(virtualRef.current + dir, dir, optionsRef.current.duration);
  };

  const jumpTo = (target: number) => {
    const o = optionsRef.current;
    const total = Math.max(1, o.count);
    const from = mod(virtualRef.current, total);
    const to = mod(target, total);
    if (to === from) return;

    let delta: number;
    if (o.loop === false) {
      delta = to - from;
    } else {
      delta = mod(to - from, total);
      if (delta > total / 2) delta -= total;
    }
    const steps = Math.abs(delta);
    const ms = Math.min(900, Math.max(o.duration, o.duration * steps * 0.75));
    commit(virtualRef.current + delta, delta > 0 ? 1 : -1, ms);
  };

  const handleWheel = (event: WheelEvent) => {
    const o = optionsRef.current;
    if (o.disabled || o.wheelMode === 'off') return;
    // 触控板捏合缩放、横向手势一律不接管
    if (event.ctrlKey) return;
    const dy = event.deltaY;
    if (!dy) return;
    if (Math.abs(event.deltaX) > Math.abs(dy)) return;
    if (o.wheelMode === 'focus') {
      const el = containerRef.current;
      const active = typeof document === 'undefined' ? null : document.activeElement;
      if (!el || !active || !el.contains(active)) return;
    }

    const dir: StepDirection = dy > 0 ? 1 : -1;
    const discrete = event.deltaMode !== 0 || Math.abs(dy) >= (o.notchThreshold ?? 80);
    const wasContinuous = lastContinuousRef.current;
    const now = Date.now();
    const gap = now - lastWheelRef.current;
    lastWheelRef.current = now;
    lastContinuousRef.current = !discrete;
    // 上一次也是离散一格 → 这是一次新的拨动（快速连拨）；否则要求足够长的间隔。
    const newGesture = gap > (o.gestureGap ?? 150) || !wasContinuous;

    // 非循环 + 已到边界 + 不在动画中：不拦截，让页面照常滚动
    if (!lockedRef.current && !canMove(dir)) return;
    event.preventDefault();

    if (lockedRef.current) {
      if (discrete && newGesture) queueStep(dir);
      if (!discrete) accRef.current = 0;
      if (!animatingRef.current) releaseWhenSilent();
      return;
    }

    if (discrete) {
      accRef.current = 0;
      step(dir);
      return;
    }

    accRef.current += dy;
    if (Math.abs(accRef.current) >= (o.threshold ?? 60)) {
      const d: StepDirection = accRef.current > 0 ? 1 : -1;
      accRef.current = 0;
      step(d);
    }
  };

  const handleKeyDown = (event: KeyboardEvent) => {
    const o = optionsRef.current;
    if (o.disabled || o.keyboard === false) return;
    const target = event.target as HTMLElement | null;
    if (target) {
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) {
        return;
      }
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
        jumpTo(0);
        return;
      case 'End':
        event.preventDefault();
        jumpTo(Math.max(0, o.count - 1));
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
    // 必须显式 passive:false —— React 合成事件 / 浏览器对 wheel 的默认被动监听无法 preventDefault
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
      queueRef.current = [];
      animatingRef.current = false;
      lockedRef.current = false;
    };
  }, []);

  // 这些方法每次渲染重建即可：内部只读 ref 与 optionsRef，不依赖闭包里的旧 props。
  const settle = () => finish(tokenRef.current);
  const next = () => step(1);
  const prev = () => step(-1);

  // 时长可能在挂载后才确定（比如 prefers-reduced-motion 是客户端才读到的），
  // 空闲时同步给 CSS transition，避免第一帧仍然用旧时长。
  React.useEffect(() => {
    if (!animatingRef.current) setMoveDuration(duration);
  }, [duration]);

  return {
    virtual,
    index: currentIndex(),
    direction,
    moveDuration,
    containerRef,
    settle,
    jumpTo,
    next,
    prev,
  };
}
