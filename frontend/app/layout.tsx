import type { Metadata } from 'next';
import SiteHeader from '@/components/site-header';
import './globals.css';

export const metadata: Metadata = {
  title: '简甄 ZhenResume — 60 秒诊断简历',
  description:
    '简甄 ZhenResume：AI 简历诊断，60 秒给出证据化的评分、逐条改写建议与下一步行动清单。',
};

/**
 * 全站骨架：**紫色画布 + 一张白色纸面**（参照 image_demo/img_1.png）。
 *
 * 需要注意的两点：
 * 1. `body` 负责紫色画布与四周留白，`.sheet` 负责白纸 —— 打印样式依赖
 *    `.sheet > header` / `.sheet > footer` 这层结构（见 globals.css 的 @media print）；
 * 2. 页脚放进了纸面内部，所以纸面高度用 min-height 撑满，不会出现白底断开。
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-brand-500">
        {/* 键盘用户按 Tab 的第一个落点 */}
        <a
          href="#main"
          className="skip-link border border-ink-900 bg-white px-3 py-2 text-sm font-semibold text-ink-950"
        >
          跳到主要内容
        </a>

        <div className="sheet">
          <SiteHeader />

          <main id="main" className="mx-auto w-full max-w-[1180px] px-5 py-10 sm:px-8 sm:py-14">
            {children}
          </main>

          <footer className="mx-auto w-full max-w-[1180px] border-t border-ink-200 px-5 pb-10 pt-6 sm:px-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <p className="max-w-3xl text-[11px] leading-relaxed text-ink-500">
                简甄 ZhenResume · 分析结果由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。
                简历与 JD 数据仅用于本次诊断，可随时在简历管理中删除。
              </p>
              <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink-400">
                v3.0
              </span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
