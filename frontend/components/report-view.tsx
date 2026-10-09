import type { ReactNode } from 'react';
import { AlertTriangle, BookOpen, ListChecks, Tags } from 'lucide-react';
import type { Report } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { FindingCard } from '@/components/finding-card';
import { RiskBadge, RISK_LEVEL_LABELS } from '@/components/risk-badge';
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
  if (score >= 80) return 'text-green-600';
  if (score >= 60) return 'text-amber-600';
  return 'text-red-600';
}

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

  return (
    <div className="space-y-6">
      {banner}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
            {report.resume_title || '未命名简历'}
          </h1>
          <p className="text-sm text-slate-500">
            目标岗位：{report.jd_title || '未指定'} · 生成于 {formatDateTime(report.created_at)}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <RiskBadge level={report.risk_level} />
            <span className="text-xs text-slate-500">
              综合风险：{RISK_LEVEL_LABELS[report.risk_level] ?? report.risk_level}
            </span>
          </div>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>

      {/* 总分 + 五维评分 */}
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 p-6 pt-6">
            <p className="text-sm text-slate-500">综合评分</p>
            <p className={`text-6xl font-semibold tabular-nums ${scoreTone(report.scores.overall)}`}>
              {report.scores.overall}
            </p>
            <p className="text-xs text-slate-400">满分 100 分</p>
            <div className="mt-2 w-full">
              <Progress value={report.scores.overall} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>五维评分</CardTitle>
          </CardHeader>
          <CardContent>
            <ScoreRadar
              dimensions={[
                { label: 'ATS 匹配', value: report.scores.ats },
                { label: '影响力', value: report.scores.impact },
                { label: '清晰度', value: report.scores.clarity },
                { label: '竞争力', value: report.scores.match },
                { label: '抗风险', value: report.scores.risk },
              ]}
            />
          </CardContent>
        </Card>
      </div>

      {/* 总评 */}
      <Card>
        <CardHeader>
          <CardTitle>诊断总评</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-relaxed text-slate-700">{report.summary || '暂无总评。'}</p>
        </CardContent>
      </Card>

      {/* 风险概览 */}
      <Card>
        <CardHeader>
          <CardTitle>风险概览</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-3 gap-3">
          {GROUP_ORDER.map((severity) => (
            <div
              key={severity}
              className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-3 text-center"
            >
              <p className={`text-2xl font-semibold tabular-nums ${GROUP_TEXT[severity]}`}>
                {counts[severity]}
              </p>
              <p className="mt-0.5 text-xs text-slate-500">{GROUP_TITLES[severity]}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* findings 分组 */}
      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-slate-400" aria-hidden />
          <h2 className="text-base font-semibold text-slate-900">逐条诊断与改写建议</h2>
          <span className="text-xs text-slate-500">共 {report.findings.length} 条</span>
        </div>

        {grouped.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50/60 px-4 py-6 text-center text-sm text-slate-500">
            本次诊断没有发现需要修改的问题。
          </p>
        ) : (
          grouped.map((group) => (
            <div key={group.severity} className="space-y-3">
              <div className="flex flex-wrap items-baseline gap-2 border-b border-slate-200 pb-2">
                <h3 className={`text-sm font-semibold ${GROUP_TEXT[group.severity]}`}>
                  {GROUP_TITLES[group.severity]}
                </h3>
                <span className="text-xs text-slate-500">
                  {group.items.length} 条 · {GROUP_DESCRIPTIONS[group.severity]}
                </span>
              </div>
              {group.items.map((finding) => (
                <FindingCard key={finding.finding_id} finding={finding} />
              ))}
            </div>
          ))
        )}
      </section>

      {/* 缺失关键词 + 行动清单 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Tags className="h-4 w-4 text-slate-400" aria-hidden />
              缺失关键词
            </CardTitle>
          </CardHeader>
          <CardContent>
            {report.missing_keywords.length === 0 ? (
              <p className="text-sm text-slate-500">目标 JD 的核心关键词已基本覆盖。</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {report.missing_keywords.map((keyword) => (
                  <Badge key={keyword} variant="yellow">
                    {keyword}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-slate-400" aria-hidden />
              下一步行动清单
            </CardTitle>
          </CardHeader>
          <CardContent>
            {report.action_items.length === 0 ? (
              <p className="text-sm text-slate-500">暂无额外行动建议。</p>
            ) : (
              <ol className="space-y-2">
                {report.action_items.map((item, index) => (
                  <li key={`${index}-${item}`} className="flex gap-2 text-sm text-slate-700">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700">
                      {index + 1}
                    </span>
                    <span className="leading-relaxed">{item}</span>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 知识库引用 */}
      {report.knowledge_refs.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-slate-400" aria-hidden />
              参考知识库
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {report.knowledge_refs.map((ref) => (
              <div
                key={ref.chunk_id}
                className="flex flex-wrap items-center gap-2 text-sm text-slate-600"
              >
                <Badge variant="outline">{ref.scope}</Badge>
                <span>{ref.title}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {/* 免责声明 */}
      <div className="flex items-start gap-2 border-t border-slate-200 pt-4 text-xs leading-relaxed text-slate-400">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <p>{report.disclaimer || '本报告由 AI 生成，仅供求职参考，不构成录用决策依据。'}</p>
      </div>
    </div>
  );
}

export default ReportView;
