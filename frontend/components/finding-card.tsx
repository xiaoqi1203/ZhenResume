'use client';

import * as React from 'react';
import { BookOpen, Check, Copy, Quote } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Finding, FindingCategory, EvidenceSource } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { RiskBadge } from '@/components/risk-badge';

const CATEGORY_LABELS: Record<FindingCategory, string> = {
  ats: 'ATS 匹配',
  impact: '影响力',
  clarity: '清晰度',
  risk: '风险',
  gap: '能力差距',
};

const SOURCE_LABELS: Record<EvidenceSource, string> = {
  resume: '简历原文',
  jd: '目标 JD',
  knowledge: '知识库',
};

export const FINDING_CATEGORY_LABELS = CATEGORY_LABELS;
export const EVIDENCE_SOURCE_LABELS = SOURCE_LABELS;

export interface FindingCardProps {
  finding: Finding;
  className?: string;
}

export function FindingCard({ finding, className }: FindingCardProps) {
  const [copied, setCopied] = React.useState(false);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(finding.rewritten_text);
      } else {
        throw new Error('clipboard unavailable');
      }
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  const evidence = finding.evidence;
  const span = evidence?.span;
  // 旧实现无条件渲染 `[{span?.[0] ?? 0}, {span?.[1] ?? 0}]`，于是没有 span 的条目
  // （规则层的"结构性建议"、引文定位失败的条目）全都显示成 `[0, 0]` —— 看起来像程序坏了。
  // 现在只有真的有 span 才显示，且用「定位：第 X–Y 字」这种用户能懂的说法。
  const hasSpan =
    Array.isArray(span) && span.length === 2 && (span[1] as number) > (span[0] as number);
  const unverified = Boolean(evidence?.unverified) || (!hasSpan && Boolean(evidence?.quote));

  return (
    <article
      className={cn('border border-ink-200 bg-white p-4 sm:p-5', className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <RiskBadge level={finding.severity} />
        <Badge variant="outline">{CATEGORY_LABELS[finding.category] ?? finding.category}</Badge>
        <h3 className="w-full text-[15px] font-extrabold leading-snug tracking-tight text-ink-950 sm:w-auto">
          {finding.title}
        </h3>
      </header>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <section className="border border-ink-200 bg-ink-50 p-3">
          <p className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
            简历原文
          </p>
          <blockquote className="flex gap-2 text-[13px] leading-relaxed text-ink-800">
            <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-400" aria-hidden />
            <span className="whitespace-pre-wrap break-words">
              {finding.original_text || '（原文缺失）'}
            </span>
          </blockquote>
        </section>

        <section className="border border-brand-300 bg-ink-50 p-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-700">
              改写建议
            </p>
            <button
              type="button"
              onClick={handleCopy}
              className={cn(
                'inline-flex items-center gap-1 border px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] transition-colors',
                copied
                  ? 'border-green-600 text-green-700'
                  : 'border-brand-500 text-brand-700 hover:bg-brand-500 hover:text-white',
              )}
            >
              {copied ? (
                <Check className="h-3 w-3" aria-hidden />
              ) : (
                <Copy className="h-3 w-3" aria-hidden />
              )}
              {copied ? '已复制' : '复制改写句'}
            </button>
          </div>
          <p className="whitespace-pre-wrap break-words font-mono text-[13px] leading-relaxed text-ink-950">
            {finding.rewritten_text || '（暂无改写句）'}
          </p>
        </section>
      </div>

      <div className="mt-3 space-y-1.5 text-sm">
        <p className="text-[13px] leading-relaxed text-ink-800">
          <span className="mr-1.5 text-[10.5px] font-bold uppercase tracking-[0.1em] text-ink-600">
            问题
          </span>
          {finding.issue}
        </p>
        <p className="text-[13px] leading-relaxed text-ink-800">
          <span className="mr-1.5 text-[10.5px] font-bold uppercase tracking-[0.1em] text-ink-600">
            建议
          </span>
          {finding.suggestion}
        </p>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-ink-500">
          <span>证据来源：{SOURCE_LABELS[evidence?.source] ?? evidence?.source ?? '未标注'}</span>
          {evidence?.field ? (
            <>
              <span className="text-ink-300">/</span>
              <span className="font-mono">{evidence.field}</span>
            </>
          ) : null}
          {hasSpan ? (
            <>
              <span className="text-ink-300">/</span>
              <span className="font-mono">
                定位第 {(span as [number, number])[0]}–{(span as [number, number])[1]} 字
              </span>
            </>
          ) : null}
        </p>
        {evidence?.quote ? (
          <p className="border-l-2 border-ink-300 bg-ink-50 px-2.5 py-1.5 text-[11.5px] leading-relaxed text-ink-600">
            引用：「{evidence.quote}」
          </p>
        ) : (
          <p className="text-[11px] leading-relaxed text-ink-400">
            该结论由规则直接判定（如覆盖率、篇幅、时间线计算），不针对某一句原文，因此没有引文。
          </p>
        )}
        {unverified && evidence?.quote ? (
          <p className="text-[11px] leading-relaxed text-amber-700">
            ⚠ 这句引文未能在原文中精确定位（可能是 PDF 排版或换行导致），仅供参考。
          </p>
        ) : null}
        {finding.citations && finding.citations.length > 0 ? (
          <div className="border border-brand-300 bg-ink-50 px-2.5 py-2">
            <p className="mb-1 flex items-center gap-1 text-[10.5px] font-bold uppercase tracking-[0.1em] text-brand-700">
              <BookOpen className="h-3 w-3" aria-hidden />
              知识库依据（Agent 检索 · 已逐字校验）
            </p>
            <ul className="space-y-1">
              {finding.citations.map((citation) => (
                <li key={`${citation.chunk_id}-${citation.quote.slice(0, 12)}`} className="text-[11.5px] leading-relaxed text-ink-600">
                  <span className="text-brand-700">《{citation.doc_title || '知识库'}》</span>「
                  {citation.quote.length > 90 ? `${citation.quote.slice(0, 90)}…` : citation.quote}」
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export default FindingCard;