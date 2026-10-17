'use client';

import * as React from 'react';
import { AlertTriangle, Check, CreditCard, Loader2 } from 'lucide-react';
import { ApiError, createOrder, listPlans } from '@/lib/api';
import type { Plan } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState, ErrorBanner } from '@/components/states';
import { cn } from '@/lib/utils';

/**
 * 套餐分两组展示。
 * 原先 7 张卡片挤在一个 3 列网格里，最后一张会孤零零留在第三行；
 * 按「个人 / HR 团队」分组后，每组内部的卡片数都能排满，读者也更容易定位自己属于哪一类。
 */
const PLAN_GROUPS = [
  {
    key: 'personal',
    eyebrow: '个人求职者',
    title: '先用免费额度诊断一次，再决定要不要付费',
    description: '免费额度可以完整体验一次五维评分与问题清单；深度报告包含逐条改写建议与分享链接。',
    columns: 'lg:grid-cols-3',
  },
  {
    key: 'hr',
    eyebrow: 'HR 与团队',
    title: '批量初筛，把小时级筛选压到分钟级',
    description: '上传一份 JD 与多份简历，按统一标准并行分析并按总分排序，可导出 CSV 交给面试官复核。',
    columns: 'lg:grid-cols-2',
  },
] as const;

function groupOfPlan(code: string): (typeof PLAN_GROUPS)[number]['key'] {
  return code.startsWith('hr_') ? 'hr' : 'personal';
}

/** 契约 §6.1 写死的 7 个套餐（后端不可用时的静态兜底）。 */
const FALLBACK_PLANS: Plan[] = [
  {
    code: 'free',
    name: '免费体验',
    price: 0,
    currency: 'CNY',
    period: 'once',
    quota: 3,
    features: ['3 次基础诊断', '五维评分', '基础改写建议'],
  },
  {
    code: 'single_basic',
    name: '单份基础报告',
    price: 9.9,
    currency: 'CNY',
    period: 'once',
    quota: 1,
    features: ['完整维度评分', '逐条问题清单', 'Markdown 导出'],
  },
  {
    code: 'single_deep',
    name: '单份深度报告',
    price: 19.9,
    currency: 'CNY',
    period: 'once',
    quota: 1,
    features: ['完整维度评分', '逐条改写建议', '分享链接', 'Markdown 导出'],
  },
  {
    code: 'monthly_basic',
    name: '月度基础订阅',
    price: 39,
    currency: 'CNY',
    period: 'month',
    quota: -1,
    features: ['不限次诊断', '历史报告对比', '关键词趋势'],
  },
  {
    code: 'monthly_pro',
    name: '月度专业订阅',
    price: 99,
    currency: 'CNY',
    period: 'month',
    quota: -1,
    features: ['不限次诊断', '历史报告对比', '优先算力队列', '定制知识库'],
  },
  {
    code: 'hr_basic',
    name: 'HR 基础套餐',
    price: 999,
    currency: 'CNY',
    period: 'month',
    quota: 100,
    features: ['100 份/月', '批量排序', 'CSV 导出'],
  },
  {
    code: 'hr_pro',
    name: 'HR 专业套餐',
    price: 2999,
    currency: 'CNY',
    period: 'month',
    quota: 500,
    features: ['500 份/月', '批量排序', '开放 API', '定制知识库'],
  },
];

function describeError(error: unknown): { message: string; offline: boolean } {
  if (error instanceof ApiError) {
    return { message: error.message, offline: error.code === 'NETWORK_ERROR' };
  }
  if (error instanceof Error) return { message: error.message, offline: false };
  return { message: '发生未知错误，请稍后重试', offline: false };
}

function formatPrice(plan: Plan): string {
  if (plan.price === 0) return '免费';
  return `¥ ${plan.price}`;
}

function quotaText(plan: Plan): string {
  if (plan.quota < 0) return '不限次';
  return `含 ${plan.quota} 份`;
}

const PlanSkeleton = () => (
  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
    {Array.from({ length: 6 }).map((_, index) => (
      <Skeleton key={index} className="h-64 w-full" />
    ))}
  </div>
);

export default function PricingPage() {
  const [plans, setPlans] = React.useState<Plan[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [usingFallback, setUsingFallback] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [buyingCode, setBuyingCode] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await listPlans();
      const items = result.items ?? [];
      if (items.length === 0) {
        setPlans(FALLBACK_PLANS);
        setUsingFallback(true);
        setNotice('后端暂未返回套餐数据，当前展示默认定价。');
      } else {
        setPlans(items);
        setUsingFallback(false);
      }
    } catch (err) {
      const described = describeError(err);
      setPlans(FALLBACK_PLANS);
      setUsingFallback(true);
      setError(described.message);
      setNotice('当前展示默认定价，后端未连接。');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const handleBuy = async (plan: Plan) => {
    setNotice(null);
    setError(null);

    if (plan.price === 0) {
      setNotice('免费套餐无需下单，回到首页上传简历即可开始 3 次基础诊断。');
      return;
    }

    setBuyingCode(plan.code);
    try {
      const order = await createOrder({ plan_code: plan.code, provider: 'stripe' });
      setNotice(`MVP 阶段为 Mock 支付页：订单 ${order.order_no} 已创建，正在新窗口打开支付页…`);
      if (typeof window !== 'undefined' && order.pay_url) {
        window.open(order.pay_url, '_blank', 'noopener,noreferrer');
      }
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
    } finally {
      setBuyingCode(null);
    }
  };

  const renderPlanCard = (plan: Plan) => {
    const highlighted = plan.code === 'single_deep' || plan.code === 'monthly_pro';
    const buying = buyingCode === plan.code;
    return (
      <Card
              key={plan.code}
              className={cn(
                'flex flex-col',
                highlighted && 'border-ink-950 shadow-hard',
              )}
            >
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>{plan.name}</CardTitle>
            {highlighted ? <Badge variant="default">推荐</Badge> : null}
          </div>
          <p className="flex items-baseline gap-1.5">
            <span className="tnum text-[30px] font-semibold leading-none tracking-tight text-ink-900">
              {formatPrice(plan)}
            </span>
            <span className="text-xs text-ink-500">
              {plan.period === 'month' ? '/ 月' : '/ 次'} · {plan.currency}
            </span>
          </p>
          <p className="text-xs text-ink-500">{quotaText(plan)}</p>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col justify-between gap-4">
          <ul className="space-y-2">
            {plan.features.map((feature) => (
              <li key={feature} className="flex items-start gap-2 text-sm text-ink-700">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-950" aria-hidden strokeWidth={3} />
                {feature}
              </li>
            ))}
          </ul>
          <Button
            className="w-full"
            variant={highlighted ? 'default' : 'outline'}
            disabled={buying}
            onClick={() => void handleBuy(plan)}
          >
            {buying ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                正在创建订单…
              </>
            ) : (
              <>
                <CreditCard className="h-4 w-4" aria-hidden />
                {plan.price === 0 ? '免费开始' : '立即购买'}
              </>
            )}
          </Button>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="space-y-8">
      <header className="space-y-3 border-b border-ink-200 pb-8">
        <p className="inline-flex items-center gap-2 text-xs font-medium tracking-wide text-ink-500">
          <span className="h-1.5 w-1.5 bg-brand-500" aria-hidden />
          MVP 阶段为 Mock 支付页
        </p>
        <h1 className="text-[30px] leading-tight sm:text-[36px]">选择适合你的诊断套餐</h1>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-600">
          免费即可体验基础诊断；深度报告提供逐条改写建议与分享链接；HR 套餐支持批量初筛与 CSV 导出。
        </p>
      </header>

      {usingFallback ? (
        <div className="flex items-start gap-2 rounded-[10px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
          <p>{notice ?? '当前展示默认定价，后端未连接。'}</p>
        </div>
      ) : null}

      {error ? <ErrorBanner message={error} onRetry={() => void load()} /> : null}

      {notice && !usingFallback ? (
        <div className="border border-ink-300 bg-ink-50 px-4 py-3 text-[12px] leading-relaxed text-ink-700">
          {notice}
        </div>
      ) : null}

      {loading ? (
        <PlanSkeleton />
      ) : plans.length === 0 ? (
        <EmptyState title="暂无可购买的套餐" description="后端没有返回套餐数据，请稍后重试。" />
      ) : (
        <div className="space-y-12">
          {PLAN_GROUPS.map((group) => {
            const groupPlans = plans.filter((plan) => groupOfPlan(plan.code) === group.key);
            if (groupPlans.length === 0) return null;
            return (
              <section key={group.key} className="space-y-5">
                <div className="border-t border-ink-200 pt-6">
                  <p className="text-xs font-medium tracking-wide text-ink-400">{group.eyebrow}</p>
                  <h2 className="mt-2 text-[22px] leading-tight">{group.title}</h2>
                  <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-500">
                    {group.description}
                  </p>
                </div>
                <div className={cn('grid gap-4 sm:grid-cols-2', group.columns)}>
                  {groupPlans.map(renderPlanCard)}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <p className="border-t border-ink-200 pt-6 text-xs leading-relaxed text-ink-400">
        支付由第三方渠道处理，简甄不存储银行卡信息。订单与支付回调接口遵循 API 契约 §6.1，
        MVP 阶段 <code className="font-mono">pay_url</code> 指向 Mock 支付页。
      </p>
    </div>
  );
}
