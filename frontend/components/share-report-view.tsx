'use client';

import * as React from 'react';
import Link from 'next/link';
import { Eye } from 'lucide-react';
import type { SharedFinding, SharedReport } from '@/lib/types';
import { buttonVariants } from '@/lib/button-variants';
import { Badge } from '@/components/ui/badge';
import { RiskBadge } from '@/components/risk-badge';

const GROUPS = [
  { key: 'red', title: '高风险', desc: '必须修改，会直接影响 HR 与 ATS 的判断。' },
  { key: 'yellow', title: '需优化', desc: '建议优化，改完能明显提升通过率。' },
  { key: 'green', title: '表现良好', desc: '已经做得不错，保持即可。' },
] as const;

const SEVERITY_TEXT: Record<string, string> = {
  red: 'text-red-700',
  yellow: 'text-amber-700',
  green: 'text-green-700',
};

const DIMENSIONS = [
  { key: 'ats', label: 'ATS 匹配', weight: '30%' },
  { key: 'impact', label: '影响力', weight: '25%' },
  { key: 'clarity', label: '清晰度', weight: '20%' },
  { key: 'match', label: '竞争力差距', weight: '15%' },
  { key: 'risk', label: '风险安全度', weight: '10%' },
] as const;

function formatDateTime(iso: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function scoreTone(score: number): string {
  if (score >= 80) return 'text-green-700';
  if (score >= 60) return 'text-amber-700';
  return 'text-red-700';
}

export interface ShareReportViewProps {
  report: SharedReport;
}

/** 分享页专用视图：只呈现「结论」，不渲染完整报告里的证据定位与改写对照。 */
export function ShareReportView({ report }: ShareReportViewProps) {
  const grouped = GROUPS.map((group) => ({
    ...group,
    items: report.findings.filter((item) => item.severity === group.key),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="space-y-10">
      {/* 只读横幅 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border border-ink-200 bg-ink-100 px-4 py-3 text-[12px] text-ink-600">
        <span className="flex items-center gap-2">
          <Eye className="h-3.5 w-3.5 text-ink-400" aria-hidden />
          只读分享：这是一份简历诊断结论，由简甄 ZhenResume 生成。
          {report.include_raw ? '（含简历原文片段）' : '（不含简历原文）'}
        </span>
        <Link href="/" className={buttonVariants({ variant: 'link', size: 'sm' })}>
          我也要诊断我的简历 →
        </Link>
      </div>

      {/* 报告头 */}
      <header className="border-b border-ink-950 pb-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">诊断结论</p>
        <h1
          className="mt-2 max-w-[46rem] text-[24px] leading-[1.15] tracking-tighter text-ink-950 sm:text-[30px]"
          style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
          title={report.resume_title || '未命名简历'}
        >
          {report.resume_title || '未命名简历'}
        </h1>
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-ink-500">
          <span className="font-semibold uppercase tracking-[0.1em] text-ink-600">目标岗位</span>
          <span className="text-ink-900">{report.jd_title || '未指定'}</span>
          <span className="font-semibold uppercase tracking-[0.1em] text-ink-600">生成于</span>
          <span className="tnum text-ink-900">{formatDateTime(report.created_at)}</span>
          <RiskBadge level={report.risk_level} />
        </div>
      </header>

      {/* 总分 + 五维 */}
      <section className="grid gap-px border border-ink-950 bg-ink-950 lg:grid-cols-[300px_1fr]">
        <div className="bg-white p-6 sm:p-8">
          <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-500">综合评分</p>
          <div className="mt-3 flex items-end gap-3">
            <span className={`text-[88px] font-extrabold leading-[0.82] tracking-tightest tabular-nums ${scoreTone(report.scores.overall)}`}>
              {report.scores.overall}
            </span>
            <span className="pb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-ink-600">/ 100</span>
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-ink-500">
            由规则引擎按客观比率计算，同一份简历重复诊断分数一致。
          </p>
        </div>
        <div className="bg-white">
          {DIMENSIONS.map((dimension) => {
            const value = report.scores[dimension.key];
            return (
              <div
                key={dimension.key}
                className="flex items-center justify-between gap-4 border-b border-ink-100 px-5 py-3.5 last:border-b-0"
              >
                <span className="text-[13px] font-semibold text-ink-900">{dimension.label}</span>
                <span className="flex items-center gap-3">
                  <span className="h-1.5 w-28 bg-ink-100">
                    <span
                      className={`block h-full ${value >= 80 ? 'bg-green-600' : value >= 60 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
                    />
                  </span>
                  <span className={`tnum w-8 text-right text-[13px] font-bold ${scoreTone(value)}`}>{value}</span>
                  <span className="tnum w-8 text-right text-[10.5px] text-ink-400">{dimension.weight}</span>
                </span>
              </div>
            );
          })}
        </div>
      </section>

      {/* 总评 */}
      <section>
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">诊断总评</p>
        <p className="mt-3 max-w-4xl text-[20px] font-semibold leading-[1.5] tracking-tight text-ink-950 sm:text-[24px]">
          {report.summary || '暂无总评。'}
        </p>
      </section>

      {/* 风险概览 */}
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-ink-200 pb-3">
          <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">风险概览</h2>
          <span className="tnum text-[11px] text-ink-600">共 {report.findings.length} 条结论</span>
        </div>
        <div className="mt-px grid grid-cols-3 gap-px bg-ink-200">
          {GROUPS.map((group) => (
            <div key={group.key} className="bg-white px-4 py-5">
              <p className={`text-[36px] font-extrabold leading-none tracking-tightest tabular-nums ${SEVERITY_TEXT[group.key]}`}>
                {report.findings.filter((item) => item.severity === group.key).length}
              </p>
              <p className="mt-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-500">{group.title}</p>
            </div>
          ))}
        </div>
      </section>

      {/* 问题清单 */}
      <section className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-ink-200 pb-3">
          <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">逐条诊断</h2>
          <span className="tnum text-[11px] text-ink-600">共 {report.findings.length} 条</span>
        </div>

        {grouped.length === 0 ? (
          <p className="border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">
            本次诊断没有发现需要修改的问题。
          </p>
        ) : (
          grouped.map((group) => (
            <div key={group.key} className="space-y-3">
              <div className="flex flex-wrap items-baseline gap-3 bg-ink-100 px-3 py-2">
                <h3 className={`text-[12px] font-bold uppercase tracking-[0.1em] ${SEVERITY_TEXT[group.key]}`}>
                  {group.title}
                </h3>
                <span className="tnum text-[11px] text-ink-500">{group.items.length} 条</span>
                <span className="text-[11px] text-ink-600">{group.desc}</span>
              </div>
              {group.items.map((finding) => (
                <ShareFindingCard key={finding.finding_id} finding={finding} />
              ))}
            </div>
          ))
        )}
      </section>

      {/* 缺失关键词 / 行动清单 */}
      <div className="grid gap-px border border-ink-200 bg-ink-200 lg:grid-cols-2">
        <div className="bg-white p-5 sm:p-6">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">缺失关键词</h3>
          <div className="mt-4">
            {report.missing_keywords.length === 0 ? (
              <p className="text-sm text-ink-500">目标 JD 的核心关键词已基本覆盖。</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {report.missing_keywords.map((keyword) => (
                  <Badge key={keyword} variant="yellow">
                    {keyword}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="bg-white p-5 sm:p-6">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">下一步行动清单</h3>
          <div className="mt-4">
            {report.action_items.length === 0 ? (
              <p className="text-sm text-ink-500">暂无额外行动建议。</p>
            ) : (
              <ol className="divide-y divide-ink-200">
                {report.action_items.map((item, index) => (
                  <li key={`${index}-${item}`} className="flex gap-3 py-2.5 text-[13px] text-ink-800">
                    <span className="tnum w-5 shrink-0 text-[11px] font-bold text-brand-500">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="leading-relaxed">{item}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      </div>

      <p className="border-t border-ink-200 pt-4 text-[11px] leading-relaxed text-ink-500">{report.disclaimer}</p>
    </div>
  );
}

function ShareFindingCard({ finding }: { finding: SharedFinding }) {
  return (
    <article className="border border-ink-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-200 px-4 py-3">
        <h4 className="text-[14px] font-bold leading-snug text-ink-950">{finding.title}</h4>
        <span className={`text-[10px] font-bold uppercase tracking-[0.12em] ${SEVERITY_TEXT[finding.severity]}`}>
          {finding.severity === 'red' ? '高风险' : finding.severity === 'yellow' ? '需优化' : '表现良好'}
        </span>
      </div>
      <div className="space-y-3 px-4 py-3.5">
        {finding.original_text ? (
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">简历原文</p>
            <p className="mt-1 border-l-2 border-ink-300 pl-3 text-[12.5px] leading-relaxed text-ink-700">
              {finding.original_text}
            </p>
          </div>
        ) : null}
        <p className="text-[13px] leading-relaxed text-ink-800">{finding.issue}</p>
        {finding.suggestion ? (
          <p className="text-[12.5px] leading-relaxed text-ink-600">
            <span className="font-semibold text-ink-800">建议：</span>
            {finding.suggestion}
          </p>
        ) : null}
        {finding.rewritten_text ? (
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">改写参考</p>
            <p className="mt-1 border-l-2 border-brand-300 pl-3 text-[12.5px] leading-relaxed text-ink-800">
              {finding.rewritten_text}
            </p>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export default ShareReportView;
