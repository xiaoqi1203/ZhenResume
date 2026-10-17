'use client';

import * as React from 'react';
import { BookOpen, CheckCircle2, Compass, Search, Sparkles, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AgentTrace } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export interface AgentTracePanelProps {
  trace: AgentTrace;
  className?: string;
}

function statusLabel(trace: AgentTrace): { text: string; tone: string } {
  if (trace.status === 'done') return { text: '已完成', tone: 'text-green-700 border-green-600' };
  if (trace.status === 'truncated') return { text: '预算耗尽后收尾', tone: 'text-amber-700 border-amber-500' };
  return { text: '降级（无引用）', tone: 'text-ink-500 border-ink-300' };
}

/**
 * Agentic RAG 轨迹面板。
 *
 * 这是"Agentic RAG 能力"最直观的证据：能看到 Agent **自己决定**检索了哪些方向、
 * 哪几条被预算拦下、哪些方向没命中原所以没有引用，以及每条引用是否通过服务端校验。
 */
export function AgentTracePanel({ trace, className }: AgentTracePanelProps) {
  const queries = trace.retrieval_queries ?? [];
  const status = statusLabel(trace);
  const verified = trace.verify?.verified ?? 0;
  const total = trace.verify?.total ?? 0;
  const blocked = trace.retrieval_blocked ?? 0;

  return (
    <Card className={cn('border-ink-950', className)}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-brand-500" aria-hidden />
          智能检索过程 / Agentic RAG
          <Badge variant="outline" className={status.tone}>
            {status.text}
          </Badge>
          <span className="text-xs font-normal text-slate-500">
            由 Agent 自主决定检索方向与轮次，引用均经服务端逐字校验
          </span>
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-px border border-ink-200 bg-ink-200 sm:grid-cols-4">
          <Metric label="检索轮次" value={`${trace.retrieval_rounds ?? 0}${blocked ? ` / 拦截 ${blocked}` : ''}`} icon={Search} />
          <Metric label="模型往返" value={String(trace.model_calls ?? 0)} icon={Compass} />
          <Metric label="引用通过" value={total ? `${verified}/${total}` : '—'} icon={CheckCircle2} />
          <Metric
            label="挂载结论"
            value={trace.attach ? `${trace.attach.attached ?? 0}` : '—'}
            icon={BookOpen}
          />
        </div>

        {queries.length > 0 ? (
          <div>
            <p className="mb-2 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
              它自己拟定的检索方向（按调用顺序）
            </p>
            <ol className="space-y-1.5">
              {queries.map((query, index) => {
                const isBlocked = index >= (trace.retrieval_rounds ?? queries.length);
                return (
                  <li
                    key={`${index}-${query}`}
                    className="flex items-start gap-2 border-l-2 border-ink-200 bg-ink-50 px-2.5 py-1.5 text-[11.5px] leading-relaxed text-ink-800"
                  >
                    {isBlocked ? (
                      <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden />
                    ) : (
                      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-600" aria-hidden />
                    )}
                    <span className="break-words">
                      {query || '（空 query）'}
                      {isBlocked ? (
                        <span className="ml-1.5 text-[11px] text-amber-600">超出检索预算，未执行</span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}

        {trace.verify ? (
          <p className="text-[11px] leading-relaxed text-ink-500">
            服务端校验：chunk 不存在 {trace.verify.rejected_missing_chunk ?? 0} 条｜
            quote 无法逐字定位 {trace.verify.rejected_quote_not_found ?? 0} 条｜
            重复 {trace.verify.rejected_duplicate ?? 0} 条
          </p>
        ) : null}

        {trace.reasoning ? (
          <div className="border border-ink-200 bg-ink-50 px-3 py-2.5">
            <p className="mb-1 text-[10.5px] font-bold uppercase tracking-[0.12em] text-ink-500">
              Agent 自述（为什么这样检索 / 为什么没引用）
            </p>
            <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink-700">
              {trace.reasoning}
            </p>
          </div>
        ) : null}

        <p className="text-[10.5px] uppercase tracking-[0.08em] text-ink-600">
          成本 ¥{(trace.cost ?? 0).toFixed(4)} / tokens {trace.tokens ?? 0} / 延迟{' '}
          {Math.round((trace.latency_ms ?? 0) / 1000)}s
          {trace.truncated ? ' / 预算耗尽：结果可能不完整' : ''}
        </p>
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="bg-white px-3 py-2.5">
      <p className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.1em] text-ink-600">
        <Icon className="h-3 w-3" aria-hidden />
        {label}
      </p>
      <p className="mt-1 text-[22px] font-extrabold leading-none tracking-tight tabular-nums text-ink-950">
        {value}
      </p>
    </div>
  );
}

export default AgentTracePanel;