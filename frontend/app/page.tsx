'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, FileSearch, ListChecks, Loader2, Target } from 'lucide-react';
import { ApiError, createJd, createTask, uploadResume } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/lib/button-variants';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { ErrorBanner } from '@/components/states';
import UploadDropzone from '@/components/upload-dropzone';

type Phase = 'idle' | 'uploading' | 'parsing_jd' | 'creating_task' | 'redirecting';

const PHASE_TEXT: Record<Exclude<Phase, 'idle'>, string> = {
  uploading: '正在上传并解析简历…',
  parsing_jd: '正在解析岗位 JD…',
  creating_task: '正在创建诊断任务…',
  redirecting: '任务已创建，正在进入诊断进度页…',
};

/** 评分维度与权重取自 aaa.md §六.3，是真实口径，不是示意数据 */
const DIMENSIONS = [
  { label: 'ATS 匹配', weight: 30, note: '关键词覆盖与格式可解析性' },
  { label: '影响力', weight: 25, note: '量化成果与强动词' },
  { label: '清晰度', weight: 20, note: '结构、时态与篇幅' },
  { label: '竞争力差距', weight: 15, note: '与岗位基准画像对比' },
  { label: '风险', weight: 10, note: '时间线、矛盾与夸大表述' },
];
const MAX_WEIGHT = Math.max(...DIMENSIONS.map((d) => d.weight));

const CAPABILITIES = [
  {
    icon: FileSearch,
    title: '证据化',
    description: '每条问题都锚定简历原文的引用与字符区间，能定位、可核对，不做凭空评价。',
  },
  {
    icon: ListChecks,
    title: '可执行',
    description: '左原文、右改写句，逐条给出可直接复制的表达，并附下一步行动清单。',
  },
  {
    icon: Target,
    title: '越用越准',
    description: '结合岗位能力模型与行业知识库检索增强，随知识库沉淀持续提升判断质量。',
  },
];

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = React.useState<File | null>(null);
  const [jdText, setJdText] = React.useState('');
  const [jdTitle, setJdTitle] = React.useState('');
  const [company, setCompany] = React.useState('');
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [fileError, setFileError] = React.useState<string | null>(null);

  const busy = phase !== 'idle';
  const jdLength = jdText.trim().length;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;

    setError(null);
    if (!file) {
      setError('请先选择一份简历文件（PDF / DOCX / PNG / JPG / TXT，≤10MB）。');
      return;
    }
    if (jdLength < 20) {
      setError('请粘贴目标岗位 JD，至少 20 个字，便于做岗位匹配分析。');
      return;
    }

    try {
      setPhase('uploading');
      const resume = await uploadResume(file, file.name);

      setPhase('parsing_jd');
      const jd = await createJd({
        title: jdTitle.trim() || '目标岗位',
        company: company.trim() || undefined,
        raw_text: jdText.trim(),
      });

      setPhase('creating_task');
      const task = await createTask({ resume_id: resume.resume_id, jd_id: jd.jd_id });

      setPhase('redirecting');
      router.push(`/analyze/${task.task_id}`);
    } catch (err) {
      setError(describeError(err));
      setPhase('idle');
    }
  };

  return (
    <div className="space-y-16 sm:space-y-20">
      {/* Hero：左对齐 + 非对称，避免"居中大标题 + 三张等宽卡片"的通用模板观感 */}
      <section className="grid gap-10 pt-2 lg:grid-cols-12 lg:gap-14 lg:pt-6">
        <div className="animate-rise lg:col-span-7">
          <p className="inline-flex items-center gap-2 text-xs font-medium tracking-wide text-ink-500">
            <span className="h-1.5 w-1.5 rounded-full bg-brand-500" aria-hidden />
            上传即分析 · 无需注册即可试用
          </p>

          <h1 className="mt-5 text-[34px] leading-[1.12] sm:text-[44px] lg:text-[52px]">
            60 秒诊断简历，
            <br className="hidden sm:block" />
            让每段经历都被看见
          </h1>

          <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-ink-600">
            简甄用 8 个 Agent 依次完成解析、岗位匹配、ATS 检测、影响力评估、风险扫描与改写建议，
            给出可定位、可执行的诊断结论 —— 每条结论都能追溯回你的简历原文。
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a href="#diagnose" className={buttonVariants({ size: 'lg' })}>
              开始诊断
              <ArrowRight className="h-4 w-4" aria-hidden />
            </a>
            <Link href="/editor" className={buttonVariants({ variant: 'outline', size: 'lg' })}>
              用模板从零写简历
            </Link>
          </div>

          <p className="mt-4 max-w-lg text-xs leading-relaxed text-ink-400">
            手上还没有简历文件？编辑器里有 4 套模板和逐栏写作提示，写完可以直接发起诊断。
          </p>
        </div>

        {/* 评分口径：既填补 hero 右侧留白，也让"分数从哪来"变得透明 */}
        <aside className="lg:col-span-5 lg:pt-1">
          <div className="rounded-card border border-ink-200 bg-white p-5 sm:p-6">
            <h2 className="font-serif text-[15px] font-semibold tracking-tight text-ink-900">
              报告由这五个维度合成
            </h2>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-500">
              每一维度都由规则引擎按客观比率计算。同一份简历重复诊断分数一致，因此改完可以对比变化。
            </p>
            <dl className="mt-5 space-y-4">
              {DIMENSIONS.map((item) => (
                <div key={item.label}>
                  <div className="flex items-baseline justify-between gap-3">
                    <dt className="text-[13px] font-medium text-ink-800">{item.label}</dt>
                    <dd className="tnum text-[13px] font-semibold text-ink-400">{item.weight}%</dd>
                  </div>
                  <div className="mt-1.5 flex items-center gap-3">
                    {/* 条长按最大权重归一化，让维度之间的差别看得清 */}
                    <div className="h-1 flex-1 overflow-hidden rounded-full bg-ink-100">
                      <div
                        className="h-full rounded-full bg-brand-400"
                        style={{ width: `${(item.weight / MAX_WEIGHT) * 100}%` }}
                      />
                    </div>
                    <span className="w-[10.5rem] shrink-0 text-right text-[11px] leading-tight text-ink-400">
                      {item.note}
                    </span>
                  </div>
                </div>
              ))}
            </dl>
          </div>
        </aside>
      </section>

      {/* 诊断入口 */}
      <section id="diagnose" className="scroll-mt-20">
        <div className="border-t border-ink-200 pt-10">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs font-medium tracking-wide text-ink-400">诊断入口</p>
              <h2 className="mt-2 text-[26px] leading-tight sm:text-[30px]">
                上传简历，粘贴目标岗位
              </h2>
            </div>
            <p className="max-w-sm text-xs leading-relaxed text-ink-400">
              简历数据只用于本次诊断，可随时删除；诊断结果不构成录用决策依据。
            </p>
          </div>
        </div>

        {error ? (
          <div className="mt-6">
            <ErrorBanner message={error} onRetry={() => setError(null)} />
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="mt-6 grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <p className="tnum text-xs font-medium text-brand-600">01</p>
              <CardTitle>上传简历</CardTitle>
              <p className="text-sm text-ink-500">支持拖拽，单份不超过 10MB。</p>
            </CardHeader>
            <CardContent>
              <UploadDropzone
                file={file}
                onFileChange={setFile}
                onError={setFileError}
                disabled={busy}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <p className="tnum text-xs font-medium text-brand-600">02</p>
              <CardTitle>粘贴目标岗位 JD</CardTitle>
              <p className="text-sm text-ink-500">
                粘贴岗位职责与要求原文，用于计算匹配度与缺失关键词。
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <input
                  value={jdTitle}
                  onChange={(event) => setJdTitle(event.target.value)}
                  disabled={busy}
                  placeholder="岗位名称（选填），如：后端开发工程师"
                  aria-label="岗位名称"
                  className="h-10 w-full rounded-[10px] border border-ink-300 bg-white px-3 text-sm text-ink-900 transition-colors duration-200 ease-spring placeholder:text-ink-400 hover:border-ink-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/15 disabled:bg-ink-50"
                />
                <input
                  value={company}
                  onChange={(event) => setCompany(event.target.value)}
                  disabled={busy}
                  placeholder="公司名称（选填）"
                  aria-label="公司名称"
                  className="h-10 w-full rounded-[10px] border border-ink-300 bg-white px-3 text-sm text-ink-900 transition-colors duration-200 ease-spring placeholder:text-ink-400 hover:border-ink-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/15 disabled:bg-ink-50"
                />
              </div>
              <Textarea
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                disabled={busy}
                rows={9}
                placeholder={'岗位职责：……\n任职要求：……\n例如：熟悉 Python / MySQL / Redis，有高并发系统经验优先。'}
              />
              <p className="tnum text-right text-xs text-ink-400">
                {jdLength} 字
                {jdLength > 0 && jdLength < 20 ? '（还需至少 20 字）' : ''}
              </p>
            </CardContent>
          </Card>

          <div className="lg:col-span-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <Button
                type="submit"
                size="lg"
                disabled={busy || !file || Boolean(fileError)}
                className="w-full sm:w-auto sm:min-w-56"
              >
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    {PHASE_TEXT[phase as Exclude<Phase, 'idle'>]}
                  </>
                ) : (
                  <>
                    开始诊断
                    <ArrowRight className="h-4 w-4" aria-hidden />
                  </>
                )}
              </Button>
              <p className="text-xs leading-relaxed text-ink-400">
                通常需要 20–60 秒，期间可在进度页看到每个 Agent 的实时状态。
              </p>
            </div>
          </div>
        </form>
      </section>

      {/* 能力说明：改为带分隔线的列表，取代三张等宽卡片 */}
      <section>
        <div className="border-t border-ink-200 pt-10">
          <p className="text-xs font-medium tracking-wide text-ink-400">为什么不是打分器</p>
          <h2 className="mt-2 max-w-2xl text-[26px] leading-tight sm:text-[30px]">
            给结论，也给得出这个结论的依据
          </h2>
        </div>

        <dl className="mt-8 divide-y divide-ink-200 border-y border-ink-200">
          {CAPABILITIES.map((item) => {
            const Icon = item.icon;
            return (
              <div
                key={item.title}
                className="grid gap-3 py-6 sm:grid-cols-[auto_9rem_1fr] sm:items-baseline sm:gap-6"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100">
                  <Icon className="h-4 w-4" aria-hidden strokeWidth={1.75} />
                </span>
                <dt className="text-[15px] font-semibold text-ink-900">{item.title}</dt>
                <dd className="max-w-2xl text-sm leading-relaxed text-ink-600">{item.description}</dd>
              </div>
            );
          })}
        </dl>
      </section>
    </div>
  );
}
