import type { Metadata } from 'next';

/** 分享页不应被搜索引擎收录（内容属于用户简历诊断结论）。 */
export const metadata: Metadata = {
  title: '简历诊断结论 · 简甄 ZhenResume',
  description: '由简甄 ZhenResume 生成的一份简历诊断结论。',
  robots: { index: false, follow: false },
};

export default function ShareTokenLayout({ children }: { children: React.ReactNode }) {
  return children;
}
