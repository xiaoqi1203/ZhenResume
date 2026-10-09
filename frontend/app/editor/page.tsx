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
 * 编辑器是纯客户端页面：读 `?template=` / `?resumeId=` 需要 `useSearchParams()`，
 * 而 Next 14 要求客户端组件里的 `useSearchParams()` 必须包在 `<Suspense>` 里，
 * 否则 `next build` 的静态预渲染阶段会报错。这里由 Server Component 提供边界，
 * 预渲染阶段输出骨架，挂载后由 EditorWorkspace 读取查询参数并回填。
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

export default function EditorPage() {
  return (
    <Suspense fallback={<EditorFallback />}>
      <EditorWorkspace />
    </Suspense>
  );
}
