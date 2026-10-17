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

  return (
    <article
      className={cn(
        'rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5',
        className,
      )}
    >
      <header className="flex flex-wrap items-center gap-2">
        <RiskBadge level={finding.severity} />
        <Badge variant="outline">{CATEGORY_LABELS[finding.category] ?? finding.category}</Badge>
        <h3 className="w-full text-sm font-semibold text-slate-900 sm:w-auto">{finding.title}</h3>
      </header>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <section className="rounded-lg border border-slate-200 bg-slate-50/70 p-3">
          <p className="mb-1.5 text-xs font-medium text-slate-500">简历原文</p>
          <blockquote className="flex gap-2 text-sm leading-relaxed text-slate-700">
            <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
            <span className="whitespace-pre-wrap break-words">
              {finding.original_text || '（原文缺失）'}
            </span>
          </blockquote>
        </section>

        <section className="rounded-lg border border-brand-200 bg-brand-50/50 p-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-brand-700">改写建议</p>
            <button
              type="button"
              onClick={handleCopy}
              className={cn(
                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition-colors',
                copied
                  ? 'border-green-200 bg-green-50 text-green-700'
                  : 'border-brand-200 bg-white text-brand-700 hover:bg-brand-100',
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
          <p className="whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-slate-900">
            {finding.rewritten_text || '（暂无改写句）'}
          </p>
        </section>
      </div>

      <div className="mt-3 space-y-1.5 text-sm">
        <p className="text-slate-700">
          <span className="font-medium text-slate-500">问题：</span>
          {finding.issue}
        </p>
        <p className="text-slate-700">
          <span className="font-medium text-slate-500">建议：</span>
          {finding.suggestion}
        </p>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span>证据来源：{SOURCE_LABELS[evidence.source] ?? evidence.source}</span>
          <span className="text-slate-300">|</span>
          <span className="font-mono">{evidence.field}</span>
          <span className="text-slate-300">|</span>
          <span className="font-mono">
            [{evidence.span?.[0] ?? 0}, {evidence.span?.[1] ?? 0}]
          </span>
        </p>
        {evidence.quote ? (
          <p className="rounded-md bg-slate-50 px-2 py-1.5 text-xs text-slate-600">
            引用：「{evidence.quote}」
          </p>
        ) : null}
        {finding.citations && finding.citations.length > 0 ? (
          <div className="rounded-md border border-brand-200/70 bg-brand-50/40 px-2.5 py-2">
            <p className="mb-1 flex items-center gap-1 text-[11px] font-medium text-brand-700">
              <BookOpen className="h-3 w-3" aria-hidden />
              知识库依据（Agent 检索 · 已逐字校验）
            </p>
            <ul className="space-y-1">
              {finding.citations.map((citation) => (
                <li key={`${citation.chunk_id}-${citation.quote.slice(0, 12)}`} className="text-xs text-slate-600">
                  <span className="text-slate-500">《{citation.doc_title || '知识库'}》</span>「
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
