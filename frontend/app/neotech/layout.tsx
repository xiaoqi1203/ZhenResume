import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'NEOTECH · 竖向卡片切换',
  description:
    '深色科技风竖向卡片滑块：滚轮 / 方向键分页切换，纯 translateY 平滑位移，无翻转、无原生滚动条。',
};

export default function NeotechLayout({ children }: { children: React.ReactNode }) {
  return children;
}
