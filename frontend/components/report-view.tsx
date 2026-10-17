import type { ReactNode } from 'react';
import { AlertTriangle, BookOpen, ListChecks, Tags } from 'lucide-react';
import type { Report } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { FindingCard } from '@/components/finding-card';
import { RiskBadge, RISK_LEVEL_LABELS } from '@/components/risk-badge';
import { AgentTracePanel } from '@/components/agent-trace-panel';
import { ScoreRadar } from '@/components/score-radar';

const GROUP_ORDER = ['red', 'yellow', 'green'] as const;
const GROUP_TITLES: Record<(typeof GROUP_ORDER)[number], string> = {
  red: '高风险',
  yellow: '需优化',
  green: '表现良好',
};
const GROUP_DESCRIPTIONS: Record<(typeof GROUP_ORDER)[number], string> = {
  red: '必须修改，会直接影响 HR 与 ATS 的判断。',
  yellow: '建议优化，改完能明显提升通过率。',
  green: '已经做得不错，保持即可。',
};
const GROUP_TEXT: Record<(typeof GROUP_ORDER)[number], string> = {
  red: 'text-red-700',
  yellow: 'text-amber-700',
  green: 'text-green-700',
};
const GROUP_BAR: Record<(typeof GROUP_ORDER)[number], string> = {
  red: 'bg-red-500',
  yellow: 'bg-amber-500',
  green: 'bg-green-600',
};

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

const SCORE_DIMENSIONS = [
  { key: 'ats', label: 'ATS 匹配', weight: '30%' },
  { key: 'impact', label: '影响力', weight: '25%' },
  { key: 'clarity', label: '清晰度', weight: '20%' },
  { key: 'match', label: '竞争力差距', weight: '15%' },
  { key: 'risk', label: '风险安全度', weight: '10%' },
] as const;

export interface ReportViewProps {
  report: Report;
  /** 顶部操作区（分享页不传） */
  actions?: ReactNode;
  /** 只读分享提示 */
  banner?: ReactNode;
}

export function ReportView({ report, actions, banner }: ReportViewProps) {
  const counts = report.findings.reduce(
    (acc, finding) => {
      if (finding.severity === 'red') acc.red += 1;
      else if (finding.severity === 'yellow') acc.yellow += 1;
      else acc.green += 1;
      return acc;
    },
    { red: 0, yellow: 0, green: 0 },
  );

  const grouped = GROUP_ORDER.map((severity) => ({
    severity,
    items: report.findings.filter((finding) => finding.severity === severity),
  })).filter((group) => group.items.length > 0);

  const total = Math.max(report.findings.length, 1);

  return (
    <div className="space-y-12">
      {banner}

      {/* 报告头：标题给足字号，元信息用小号大写标签 */}
      <header className="flex flex-col gap-5 border-b border-ink-950 pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">
            诊断报告
          </p>
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
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </header>

      {/* ---------------------------------------------------------------- */}
      {/* 总分 + 五维：左侧大号数字区，右侧表格化维度 + 雷达               */}
      {/* ---------------------------------------------------------------- */}
      <section className="grid gap-px border border-ink-950 bg-ink-950 lg:grid-cols-[320px_1fr]">
        <div className="bg-white p-6 sm:p-8">
          <p className="stat-label">综合评分</p>
          <div className="mt-3 flex items-end gap-3">
            <span className={`text-[92px] font-extrabold leading-[0.82] tracking-tightest tabular-nums ${scoreTone(report.scores.overall)}`}>
              {report.scores.overall}
            </span>
            <span className="pb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-ink-600">
              / 100
            </span>
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-ink-500">
            由规则引擎按客观比率计算，同一份简历重复诊断分数一致。
            Agent 只补依据，不参与打分。
          </p>
        </div>

        <div className="bg-white">
          <div className="border-b border-ink-200 px-6 py-3">
            <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
              五维评分
            </h2>
          </div>

          <dl className="divide-y divide-ink-200">
            {SCORE_DIMENSIONS.map((dimension) => {
              const value = report.scores[dimension.key];
              return (
                <div
                  key={dimension.key}
                  className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-4 px-6 py-2.5"
                >
                  <dt className="text-[12px] font-semibold text-ink-900">{dimension.label}</dt>
                  <dd className="h-1 bg-ink-200">
                    <div
                      className={`h-full ${value >= 80 ? 'bg-green-600' : value >= 60 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${value}%` }}
                    />
                  </dd>
                  <dd className="flex items-baseline justify-end gap-1">
                    <span className="tnum text-[15px] font-extrabold text-ink-950">{value}</span>
                    <span className="text-[10px] text-ink-600">{dimension.weight}</span>
                  </dd>
                </div>
              );
            })}
          </dl>

          <div className="border-t border-ink-200 px-6 py-4">
            <ScoreRadar
              dimensions={[
                { label: 'ATS 匹配', value: report.scores.ats },
                { label: '影响力', value: report.scores.impact },
                { label: '清晰度', value: report.scores.clarity },
                { label: '竞争力', value: report.scores.match },
                { label: '抗风险', value: report.scores.risk },
              ]}
            />
          </div>
        </div>
      </section>

      {/* 总评：大字号引言式，贴参考图的粗排版 */}
      <section>
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">诊断总评</p>
        <p className="mt-3 max-w-4xl text-[22px] font-semibold leading-[1.45] tracking-tight text-ink-950 sm:text-[26px]">
          {report.summary || '暂无总评。'}
        </p>
      </section>

      {/* Agentic RAG 检索过程（仅 agentic 模式有） */}
      {report.agent_trace ? <AgentTracePanel trace={report.agent_trace} /> : null}

      {/* 风险概览：三段等宽，用反白数字块表现 */}
      <section>
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-ink-200 pb-3">
          <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">风险概览</h2>
          <span className="tnum text-[11px] text-ink-600">共 {report.findings.length} 条结论</span>
        </div>
        <div className="mt-px grid grid-cols-3 gap-px bg-ink-200">
          {GROUP_ORDER.map((severity) => (
            <div key={severity} className="bg-white px-4 py-5">
              <p className={`text-[40px] font-extrabold leading-none tracking-tightest tabular-nums ${GROUP_TEXT[severity]}`}>
                {counts[severity]}
              </p>
              <p className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-500">
                <span className={`h-1.5 w-1.5 ${GROUP_BAR[severity]}`} aria-hidden />
                {GROUP_TITLES[severity]}
              </p>
              <div className="mt-3 h-1 bg-ink-100">
                <div
                  className={`h-full ${GROUP_BAR[severity]}`}
                  style={{ width: `${(counts[severity] / total) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* findings 分组 */}
      <section className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-ink-200 pb-3">
          <h2 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
            <ListChecks className="h-3.5 w-3.5" aria-hidden />
            逐条诊断与改写建议
          </h2>
          <span className="tnum text-[11px] text-ink-600">共 {report.findings.length} 条</span>
        </div>

        {grouped.length === 0 ? (
          <p className="border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">
            本次诊断没有发现需要修改的问题。
          </p>
        ) : (
          grouped.map((group) => (
            <div key={group.severity} className="space-y-3">
              <div className="flex flex-wrap items-baseline gap-3 bg-ink-100 px-3 py-2">
                <h3 className={`text-[12px] font-bold uppercase tracking-[0.1em] ${GROUP_TEXT[group.severity]}`}>
                  {GROUP_TITLES[group.severity]}
                </h3>
                <span className="tnum text-[11px] text-ink-500">{group.items.length} 条</span>
                <span className="text-[11px] text-ink-600">{GROUP_DESCRIPTIONS[group.severity]}</span>
              </div>
              {group.items.map((finding) => (
                <FindingCard key={finding.finding_id} finding={finding} />
              ))}
            </div>
          ))
        )}
      </section>

      {/* 缺失关键词 + 行动清单 */}
      <div className="grid gap-px border border-ink-200 bg-ink-200 lg:grid-cols-2">
        <div className="bg-white p-5 sm:p-6">
          <h3 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
            <Tags className="h-3.5 w-3.5" aria-hidden />
            缺失关键词
          </h3>
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
          <h3 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
            <ListChecks className="h-3.5 w-3.5" aria-hidden />
            下一步行动清单
          </h3>
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

      {/* 知识库引用 */}
      {report.knowledge_refs.length > 0 ? (
        <section>
          <div className="border-b border-ink-200 pb-3">
            <h2 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-900">
              <BookOpen className="h-3.5 w-3.5" aria-hidden />
              参考知识库
            </h2>
          </div>
          <ul className="divide-y divide-ink-200">
            {report.knowledge_refs.map((ref) => (
              <li
                key={ref.chunk_id}
                className="flex flex-wrap items-center gap-3 py-2.5 text-[13px] text-ink-700"
              >
                <Badge variant="outline">{ref.scope}</Badge>
                <span>{ref.title}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* 免责声明 */}
      <div className="flex items-start gap-2 border-t border-ink-200 pt-4 text-[11px] leading-relaxed text-ink-600">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <p>{report.disclaimer || '本报告由 AI 生成，仅供求职参考，不构成录用决策依据。'}</p>
      </div>
    </div>
  );
}

export default ReportView;