import type { Metadata } from 'next';
import AccountCenter from '@/components/user/account-center';

export const metadata: Metadata = {
  title: '账号中心 · 简甄 ZhenResume',
  description: '查看账号信息、我的简历、订单记录与订阅方案。',
};

/** 账号中心（/user）：未登录时组件内部会跳转 /login?next=/user */
export default function UserPage() {
  return <AccountCenter />;
}
