'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, Loader2 } from 'lucide-react';
import { ApiError, createJd, createTask, uploadResume } from '@/lib/api';
import { uploadGuardMessage } from '@/lib/upload-guard';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/lib/button-variants';
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

const CAPABILITIES = [
  {
    index: '01',
    title: '证据化',
    description:
      '每条问题都锚定简历原文的引用与字符区间，能定位、可核对，不做凭空评价。结论级引用由 Agentic 检索产出，可回溯到知识库切片。',
  },
  {
    index: '02',
    title: '可执行',
    description:
      '左原文、右改写句，逐条给出可直接复制的表达，并附下一步行动清单。改写句里的数字必须来自原文，不允许编造。',
  },
  {
    index: '03',
    title: '越用越准',
    description:
      '结合岗位能力模型与行业知识库检索增强；Agent 自己决定检索方向与轮次，报告里能看到它检索了什么、哪些方向没有依据。',
  },
];

const PIPELINE = [
  '解析简历',
  '解析 JD',
  '岗位匹配',
  'ATS 检测',
  '影响力评估',
  '风险扫描',
  '改写建议',
  '质量检查',
];

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '请求失败，请稍后重试。';
}

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = React.useState<File | null>(null);
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [jdText, setJdText] = React.useState('');
  const [jdTitle, setJdTitle] = React.useState('');
  const [company, setCompany] = React.useState('');
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);

  const busy = phase !== 'idle';
  const jdLength = jdText.trim().length;

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file || busy) return;
    setError(null);

    try {
      setPhase('uploading');
      const resume = await uploadResume(file, file.name);

      // 抽不到文字（扫描件 / OCR 引擎缺失 / 空文件）时必须拦下来：
      // 放行只会得到一份"基于空文本的低分报告"，比直接报错更伤信任。
      const blocked = uploadGuardMessage(resume);
      if (blocked) {
        setError(blocked);
        setPhase('idle');
        return;
      }

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
      {/* ---------------------------------------------------------------- */}
      {/* Hero：大字号 + 非对称分栏，左标题右"评分口径"表格                  */}
      {/* ---------------------------------------------------------------- */}
      <section className="grid gap-12 lg:grid-cols-12 lg:gap-16">
        <div className="animate-rise lg:col-span-7">
          <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-500">
            <span className="h-1.5 w-1.5 bg-brand-500" aria-hidden />
            上传即分析 · 无需注册即可试用
          </p>

          <h1 className="mt-6 text-[38px] leading-[1.08] tracking-tighter text-ink-950 sm:text-[50px] lg:text-[58px]">
            60 秒诊断简历，
            <br />
            让每段经历
            <span className="text-brand-500">都被看见</span>。
          </h1>

          <p className="mt-6 max-w-xl text-[15px] leading-[1.75] text-ink-600">
            简甄用 8 个 Agent 依次完成解析、岗位匹配、ATS 检测、影响力评估、风险扫描与改写建议，
            给出可定位、可执行的诊断结论 —— 每条结论都能追溯回你的简历原文，
            关键结论还会附上知识库依据。
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a
              href="#diagnose"
              className="inline-flex h-12 flex-1 items-center justify-center gap-2 rounded-md bg-ink-950 px-6 text-[13px] font-semibold tracking-[0.02em] text-white transition-colors hover:bg-brand-500 sm:flex-none"
            >
              开始诊断
              <ArrowRight className="h-4 w-4" aria-hidden />
            </a>

          </div>



          {/* 流水线：8 格等宽一行。用 grid-cols-8 + gap-px 保底，
              不用 flex-wrap —— 8 格在 7 列宽度下必换行，末格会孤零零掉到下一行。 */}
          <div className="mt-9 grid grid-cols-8 gap-1.5">
            {PIPELINE.map((step, index) => (
              <span
                key={step}
                className={
                  index === 0
                    ? 'flex flex-col gap-1 rounded-md bg-brand-500 px-1 py-2 text-center text-[10px] leading-tight text-white sm:text-[11px]'
                    : 'flex flex-col gap-1 rounded-md bg-ink-100 px-1 py-2 text-center text-[10px] leading-tight text-ink-600 sm:text-[11px]'
                }
              >
                <span className={`tnum text-[10px] font-bold ${index === 0 ? 'text-brand-100' : 'text-ink-600'}`}>
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="whitespace-nowrap">{step}</span>
              </span>
            ))}
          </div>
        </div>

        {/* 评分口径：右栏做成"表格"而不是卡片，贴参考图的数据表观感 */}
        <aside className="lg:col-span-5">
          <div className="panel overflow-hidden">
            <div className="flex items-baseline justify-between gap-3 border-b border-ink-200 px-4 py-3">
              <h2 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-800">
                报告由这五个维度合成
              </h2>
              <span className="tnum text-[11px] font-semibold text-ink-600">100%</span>
            </div>

            <dl className="divide-y divide-ink-200/80">
              {DIMENSIONS.map((item) => (
                <div key={item.label} className="grid grid-cols-[1fr_auto] gap-x-4 px-4 py-3">
                  <dt className="text-[13px] font-semibold text-ink-900">{item.label}</dt>
                  <dd className="tnum text-[13px] font-bold text-ink-950">{item.weight}%</dd>
                  {/* 说明文字用 #444 档（ink-700），避免太浅看不清 */}
                  <p className="col-span-2 mt-0.5 text-[11.5px] leading-relaxed text-ink-700">
                    {item.note}
                  </p>
                </div>
              ))}
            </dl>

            <p className="border-t border-ink-200 px-4 py-3 text-[11.5px] leading-relaxed text-ink-600">
              每一维度都由规则引擎按客观比率计算，同一份简历重复诊断分数一致；
              Agent 只负责补依据，不参与打分。
            </p>
          </div>
        </aside>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* 诊断入口：两步，左右并排；表单控件一律直角细线                    */}
      {/* ---------------------------------------------------------------- */}
      <section id="diagnose" className="scroll-mt-24">
        <div className="flex flex-wrap items-end justify-between gap-4 border-t border-ink-200 pt-8">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">
              诊断入口
            </p>
            <h2 className="mt-2 text-[30px] leading-none sm:text-[38px]">
              上传简历，粘贴目标岗位。
            </h2>
          </div>
          <p className="max-w-sm text-[11.5px] leading-relaxed text-ink-600">
            简历数据只用于本次诊断，可随时删除；诊断结果不构成录用决策依据。
          </p>
        </div>

        {error ? (
          <div className="mt-6">
            <ErrorBanner message={error} onRetry={() => setError(null)} />
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="mt-8 grid gap-4 lg:grid-cols-2">
          <div className="panel p-5 sm:p-6">
            <div className="flex items-baseline justify-between gap-3 border-b border-ink-200 pb-3">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-800">
                01 / 上传简历
              </h3>
              <span className="text-[11px] text-ink-600">单份 ≤ 10MB</span>
            </div>
            <div className="pt-5">
              <UploadDropzone
                file={file}
                onFileChange={setFile}
                onError={setFileError}
                disabled={busy}
              />
            </div>
          </div>

          <div className="panel p-5 sm:p-6">
            <div className="flex items-baseline justify-between gap-3 border-b border-ink-200 pb-3">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-800">
                02 / 粘贴目标岗位 JD
              </h3>
              <span className="tnum text-[11px] text-ink-600">
                {jdLength} 字
                {jdLength > 0 && jdLength < 20 ? '（至少 20 字）' : ''}
              </span>
            </div>

            <div className="space-y-3 pt-5">
              <div className="grid gap-2 sm:grid-cols-2">
                <input
                  value={jdTitle}
                  onChange={(event) => setJdTitle(event.target.value)}
                  disabled={busy}
                  placeholder="岗位名称（选填）"
                  aria-label="岗位名称"
                  className="h-10 w-full rounded-md border border-ink-300 bg-white px-3 text-sm text-ink-950 placeholder:text-ink-500 focus:border-ink-950 focus:outline-none disabled:bg-ink-100"
                />
                <input
                  value={company}
                  onChange={(event) => setCompany(event.target.value)}
                  disabled={busy}
                  placeholder="公司名称（选填）"
                  aria-label="公司名称"
                  className="h-10 w-full rounded-md border border-ink-300 bg-white px-3 text-sm text-ink-950 placeholder:text-ink-500 focus:border-ink-950 focus:outline-none disabled:bg-ink-100"
                />
              </div>
              <Textarea
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                disabled={busy}
                rows={8}
                placeholder={'岗位职责：……\n任职要求：……\n例如：熟悉 Python / MySQL / Redis，有高并发系统经验优先。'}
              />
            </div>
          </div>

          <div className="panel p-5 sm:col-span-2 sm:p-6">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
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
              <p className="text-[11.5px] leading-relaxed text-ink-600">
                通常需要 20–60 秒，期间可在进度页看到每个 Agent 的实时状态。
              </p>
            </div>
          </div>
        </form>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* 能力说明：编号列表 + 反白小块，取代三张等宽卡片                    */}
      {/* ---------------------------------------------------------------- */}
      <section>
        <div className="flex flex-wrap items-end justify-between gap-4 border-t border-ink-200 pt-8">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">
              为什么不是打分器
            </p>
            <h2 className="mt-2 max-w-2xl text-[30px] leading-none sm:text-[38px]">
              给结论，也给得出这个结论的依据。
            </h2>
          </div>
          <Link href="/pricing" className="arrow-cta text-[12px] font-semibold text-ink-950">
            查看定价
          </Link>
        </div>

        <dl className="mt-8 divide-y divide-ink-200 border-y border-ink-200">
          {CAPABILITIES.map((item) => (
            <div
              key={item.title}
              className="grid gap-3 py-7 sm:grid-cols-[3rem_8rem_1fr] sm:items-baseline sm:gap-6"
            >
              <span className="chip-invert w-fit">{item.index}</span>
              <dt className="text-[18px] font-extrabold tracking-tight text-ink-950">
                {item.title}
              </dt>
              <dd className="max-w-2xl text-[13px] leading-relaxed text-ink-600">
                {item.description}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* 结尾 CTA：黑底通栏，参考图的黑白对比                                 */}
      {/* ---------------------------------------------------------------- */}
      <section className="flex flex-wrap items-center justify-between gap-6 bg-ink-950 px-6 py-10 sm:px-10">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-600">
            现在开始
          </p>
          <h2 className="mt-2 max-w-xl text-[26px] leading-tight text-white sm:text-[32px]">
            这一次，把简历交给能给出证据的诊断。
          </h2>
        </div>
        <a
          href="#diagnose"
          className="inline-flex h-12 items-center justify-center border border-white bg-white px-7 text-[13px] font-semibold uppercase tracking-[0.06em] text-ink-950 transition-colors hover:border-brand-500 hover:bg-brand-500 hover:text-white"
        >
          上传简历
          <ArrowRight className="ml-2 h-4 w-4" aria-hidden />
        </a>
      </section>
    </div>
  );
}