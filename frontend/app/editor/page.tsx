import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import EditorStart from '@/components/editor/editor-start';

export const metadata: Metadata = {
  title: '开始简历 — 简甄 ZhenResume',
  description:
    '先选模板与内容起点（都可以跳过），再进入简历编辑器：按结构化字段填写、实时预览，导出 Markdown / DOCX / PDF 或直接发起诊断。',
};

/**
 * 简历编辑的开始页：只承载「选模板」「从已有简历开始」两个可选决策，
 * 决策完成后跳到 `/editor/write`（真正的编辑器）。
 *
 * 旧链接兼容：`/editor?resumeId=xxx`（账号中心 / 报告页历史链接）过去会直接打开编辑器，
 * 现在在服务端重定向到 `/editor/write?start=resume&resumeId=xxx`，保持"继续编辑"一步到位。
 */
export default function EditorStartPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const rawResumeId = searchParams?.resumeId;
  const rawTemplate = searchParams?.template;
  const resumeId = Array.isArray(rawResumeId) ? rawResumeId[0] : rawResumeId;
  const template = Array.isArray(rawTemplate) ? rawTemplate[0] : rawTemplate;

  if (resumeId) {
    const params = new URLSearchParams({ start: 'resume', resumeId });
    if (template) params.set('template', template);
    redirect(`/editor/write?${params.toString()}`);
  }

  return <EditorStart initialTemplateCode={template ?? null} />;
}
