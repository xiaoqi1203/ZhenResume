import { Suspense } from 'react';
import type { Metadata } from 'next';
import { Skeleton } from '@/components/ui/skeleton';
import EditorWorkspace from '@/components/editor/editor-workspace';

export const metadata: Metadata = {
  title: '简历编辑器 — 简甄 ZhenResume',
  description:
    '按结构化字段填写简历，实时预览，一键导出 Markdown / DOCX / PDF，并直接用这份简历发起诊断。',
};

/**
 * 真正的简历编辑页（`/editor/write`）。
 *
 * 与 `/editor`（开始页）的分工：
 * - `/editor` 只做「模板 / 起点」两个决策，让用户先明确要做什么；
 * - 本页只做写作本身：左侧结构化表单、右侧实时预览、保存导出与发起诊断。
 *
 * 查询参数（由开始页生成）：
 * - `start=blank | draft | resume`：内容起点；
 * - `template=code`：覆盖模板（`resume` 起点下也允许换版式）；
 * - `resumeId=xxx`：`start=resume` 时载入的简历。
 *
 * 客户端组件里用了 `useSearchParams()`，Next 14 要求外层必须有 `<Suspense>`；
 * 这里由 Server Component 提供边界，预渲染阶段输出骨架。
 */
function EditorFallback() {
  return (
    <div className="space-y-6" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-80" />
      </div>
      <Skeleton className="h-40 w-full rounded-xl" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,46%)]">
        <div className="space-y-4">
          <Skeleton className="h-36 w-full rounded-xl" />
          <Skeleton className="h-36 w-full rounded-xl" />
        </div>
        <Skeleton className="h-[520px] w-full rounded-xl" />
      </div>
      <p className="sr-only">简历编辑器加载中…</p>
    </div>
  );
}

export default function EditorWritePage() {
  return (
    <Suspense fallback={<EditorFallback />}>
      <EditorWorkspace />
    </Suspense>
  );
}