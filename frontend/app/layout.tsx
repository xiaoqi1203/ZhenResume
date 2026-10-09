import type { Metadata } from 'next';
import SiteHeader from '@/components/site-header';
import './globals.css';

export const metadata: Metadata = {
  title: '简甄 ZhenResume — 60 秒诊断简历',
  description:
    '简甄 ZhenResume：AI 简历诊断，60 秒给出证据化的评分、逐条改写建议与下一步行动清单。',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-ink-50">
        {/* 键盘用户按 Tab 的第一个落点 */}
        <a
          href="#main"
          className="skip-link rounded-[10px] border border-ink-300 bg-white px-3 py-2 text-sm font-medium text-ink-800 shadow-pop"
        >
          跳到主要内容
        </a>
        <SiteHeader />
        <main id="main" className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
          {children}
        </main>
        <footer className="mx-auto w-full max-w-6xl border-t border-ink-200 px-4 pb-10 pt-6 sm:px-6">
          <p className="max-w-3xl text-xs leading-relaxed text-ink-400">
            简甄 ZhenResume · 分析结果由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。
            简历与 JD 数据仅用于本次诊断，可随时在简历管理中删除。
          </p>
        </footer>
      </body>
    </html>
  );
}
