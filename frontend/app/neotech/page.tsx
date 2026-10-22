'use client';

import * as React from 'react';
import { VerticalCardSlider } from '@/components/vertical-card-slider/vertical-card-slider';

type ShowcaseCard = {
  word: string;
  code: string;
  line: string;
  note: string;
  accent: string;
};

const CARDS: ShowcaseCard[] = [
  { word: 'NEOTECH', code: 'NT—001', line: 'Adaptive Interface', note: '实时感知 · 自适应界面', accent: '#22d3ee' },
  { word: 'INNOVATE', code: 'IV—002', line: 'Rapid Prototyping', note: '快速原型 · 迭代验证', accent: '#60a5fa' },
  { word: 'CATALYST', code: 'CT—003', line: 'Signal Amplifier', note: '信号放大 · 增长触发', accent: '#a78bfa' },
  { word: 'QUANTUM', code: 'QT—004', line: 'Parallel Compute', note: '并行计算 · 概率推理', accent: '#e879f9' },
  { word: 'FUSION', code: 'FS—005', line: 'Core Integration', note: '内核整合 · 统一调度', accent: '#fbbf24' },
];

function pad(n: number) {
  return String(n).padStart(2, '0');
}

function ShowcaseCard({ card, index, total }: { card: ShowcaseCard; index: number; total: number }) {
  const corners = [
    'left-4 top-4 border-l border-t',
    'right-4 top-4 border-r border-t',
    'bottom-4 left-4 border-b border-l',
    'bottom-4 right-4 border-b border-r',
  ];

  return (
    <article
      className="relative flex h-full w-full flex-col overflow-hidden rounded-[18px] border border-white/10 bg-[#07090f]"
      style={{
        boxShadow: `0 40px 120px -60px ${card.accent}66, inset 0 1px 0 rgba(255,255,255,0.06)`,
      }}
    >
      {/* 顶部强调线 */}
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-px"
        style={{ background: `linear-gradient(90deg, transparent, ${card.accent}, transparent)` }}
      />
      {/* 卡片内的暗色光晕 */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: `radial-gradient(120% 90% at 50% -10%, ${card.accent}22, transparent 60%)` }}
      />
      {/* 角落刻线 */}
      {corners.map((cls) => (
        <span key={cls} aria-hidden className={`pointer-events-none absolute h-3 w-3 border-white/20 ${cls}`} />
      ))}
      {/* 背景巨大的幽灵序号 */}
      <span
        aria-hidden
        className="pointer-events-none absolute -bottom-10 -right-4 select-none text-[220px] font-extrabold leading-none tracking-tighter text-white/[0.03]"
      >
        {pad(index + 1)}
      </span>

      <div className="relative z-10 flex items-center justify-between px-6 py-5 font-mono text-[10px] uppercase tracking-[0.24em] sm:px-9 sm:py-6">
        <span style={{ color: card.accent }}>{`// node ${pad(index + 1)}`}</span>
        <span className="text-white/35">{card.code}</span>
      </div>

      <div className="relative z-10 flex flex-1 flex-col items-center justify-center px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.4em] text-white/40">{card.line}</span>
        <h2
          className="mt-5 text-center text-[clamp(42px,8.2vw,104px)] font-extrabold uppercase leading-[0.95] tracking-tight text-white"
          style={{ textShadow: `0 0 70px ${card.accent}66` }}
        >
          {card.word}
        </h2>
        <div className="mt-7 flex items-center gap-3">
          <span className="h-px w-10" style={{ background: card.accent }} />
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/50">{card.note}</span>
          <span className="h-px w-10" style={{ background: card.accent }} />
        </div>
      </div>

      <div className="relative z-10 flex items-center justify-between border-t border-white/10 px-6 py-4 font-mono text-[10px] uppercase tracking-[0.22em] text-white/35 sm:px-9">
        <span className="flex items-center gap-2">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full" style={{ background: card.accent }} />
          System Online
        </span>
        <span>
          {pad(index + 1)} / {pad(total)}
        </span>
      </div>
    </article>
  );
}

export default function NeotechPage() {
  const [active, setActive] = React.useState(0);
  const accent = CARDS[Math.min(active, CARDS.length - 1)].accent;

  // 需求：不要页面原生滚动条 —— 整页锁定，切换只发生在卡片容器内部。
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

  return (
    <div className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-[#05060a] text-white antialiased">
      {/* 背景：径向光晕 + 网格 + 扫描线 */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(900px 520px at 50% -12%, rgba(34,211,238,0.14), transparent 62%), radial-gradient(760px 520px at 92% 112%, rgba(139,92,246,0.12), transparent 62%)',
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              'linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px)',
            backgroundSize: '72px 72px',
            maskImage: 'radial-gradient(ellipse at center, black 28%, transparent 78%)',
            WebkitMaskImage: 'radial-gradient(ellipse at center, black 28%, transparent 78%)',
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              'repeating-linear-gradient(0deg, rgba(255,255,255,0.022) 0px, rgba(255,255,255,0.022) 1px, transparent 1px, transparent 3px)',
          }}
        />
      </div>

      {/* 顶部进度线 */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 h-px bg-white/10">
        <div
          className="h-full transition-all duration-500"
          style={{
            width: `${((active + 1) / CARDS.length) * 100}%`,
            background: `linear-gradient(90deg, transparent, ${accent})`,
          }}
        />
      </div>

      <header className="relative z-10 flex shrink-0 items-center justify-between gap-4 px-5 py-5 sm:px-10">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center bg-white text-[15px] font-black leading-none text-[#05060a]">
            N
          </span>
          <div className="leading-none">
            <p className="text-[15px] font-extrabold tracking-tight">
              NEOTECH<span className="align-super text-[8px] font-medium text-white/40">®</span>
            </p>
            <p className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.3em] text-white/35">
              Interface System / v2.4
            </p>
          </div>
        </div>

        <div className="flex items-center gap-5 font-mono text-[10px] uppercase tracking-[0.28em] text-white/40">
          <span className="hidden items-center gap-2 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: accent }} />
            Signal Locked
          </span>
          <span className="text-white/75">
            {pad(active + 1)} <span className="text-white/25">/ {pad(CARDS.length)}</span>
          </span>
        </div>
      </header>

      <main className="relative z-10 flex min-h-0 flex-1 items-center justify-center px-4 sm:px-8">
        <div className="w-[min(1000px,92vw)]" style={{ height: 'min(540px, calc(100dvh - 260px))' }}>
          <VerticalCardSlider
            items={CARDS.map((card, i) => (
              <ShowcaseCard key={card.word} card={card} index={i} total={CARDS.length} />
            ))}
            ariaLabel="NEOTECH 卡片展示"
            showDots={false}
            onChange={setActive}
            className="h-full rounded-[18px]"
          />
        </div>
      </main>

      {/* 右侧序号导轨 */}
      <div
        aria-hidden
        className="pointer-events-none absolute right-4 top-1/2 z-10 hidden -translate-y-1/2 flex-col items-end gap-4 sm:flex sm:right-8"
      >
        {CARDS.map((card, i) => (
          <div key={card.word} className="flex items-center justify-end gap-3">
            <span
              className="font-mono text-[9px] tracking-[0.2em]"
              style={{ color: i === active ? card.accent : 'rgba(255,255,255,0.25)' }}
            >
              {pad(i + 1)}
            </span>
            <span
              className="h-px transition-all duration-500"
              style={{
                width: i === active ? 34 : 12,
                background: i === active ? card.accent : 'rgba(255,255,255,0.18)',
              }}
            />
          </div>
        ))}
      </div>

      <footer className="relative z-10 flex shrink-0 flex-col gap-4 px-5 pb-6 pt-4 font-mono text-[10px] uppercase tracking-[0.26em] text-white/40 sm:flex-row sm:items-center sm:justify-between sm:px-10 sm:pb-8">
        <div className="flex items-center gap-3">
          <span className="border border-white/20 px-2 py-1 text-white/70">Scroll</span>
          <span className="text-white/25">or</span>
          <span className="border border-white/20 px-2 py-1 text-white/70">↑ ↓</span>
          <span>to navigate</span>
        </div>

        <div className="flex items-center gap-1.5">
          {CARDS.map((card, i) => (
            <span
              key={card.word}
              className="h-[2px] transition-all duration-500"
              style={{
                width: i === active ? 36 : 16,
                background: i === active ? card.accent : 'rgba(255,255,255,0.18)',
              }}
            />
          ))}
        </div>

        <p className="text-white/30">© 2026 NEOTECH LABS</p>
      </footer>
    </div>
  );
}
