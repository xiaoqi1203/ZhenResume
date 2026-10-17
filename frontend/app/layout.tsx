import type { Metadata } from 'next';
import SiteHeader from '@/components/site-header';
import './globals.css';

export const metadata: Metadata = {
  title: '简甄 ZhenResume — 60 秒诊断简历',
  description:
    '简甄 ZhenResume：AI 简历诊断，60 秒给出证据化的评分、逐条改写建议与下一步行动清单。',
};

/**
 * 全站骨架（V3.1）：
 * - **白底铺满**，不再有紫色画布与两侧紫边（那是主要视觉干扰源）；
 * - 模块之间靠「极淡灰卡片 + 发丝线」区分，而不是靠彩色背景；
 * - 紫色仅出现在标题关键词、按钮与选中态，正文永远不用紫色。
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-white">
        {/* 键盘用户按 Tab 的第一个落点 */}
        <a
          href="#main"
          className="skip-link rounded-md border border-ink-900 bg-white px-3 py-2 text-sm font-semibold text-ink-950"
        >
          跳到主要内容
        </a>

        <div className="sheet">
          <SiteHeader />

          <main id="main" className="mx-auto w-full max-w-[1120px] px-5 py-12 sm:px-8 sm:py-16">
            {children}
          </main>

          <footer className="mx-auto w-full max-w-[1120px] border-t border-ink-200 px-5 pb-10 pt-6 sm:px-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <p className="max-w-3xl text-[11px] leading-relaxed text-ink-500">
                简甄 ZhenResume · 分析结果由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。
                简历与 JD 数据仅用于本次诊断，可随时在简历管理中删除。
              </p>
              <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink-600">
                v3.1
              </span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}