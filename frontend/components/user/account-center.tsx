'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { deleteResume, listResumes } from '@/lib/api';
import type { ResumeSummary } from '@/lib/types';
import { authApi, displayName, type AccountSummary, type AuthSession } from '@/lib/auth';

/**
 * 账号中心（/user）
 * ------------------------------------------------------------------
 * 左侧：账号信息 / 我的简历 / 订单记录 / 订阅方案；右侧：对应内容。
 *
 * 数据来源：
 * - 账号与额度：`authApi.me()`（GET /api/v1/auth/me，需要 Bearer token）
 * - 修改邮箱：PATCH /api/v1/auth/me
 * - 修改密码：POST /api/v1/auth/change-password（旧 token 全部失效，后端回一份新的）
 * - 我的简历：`listResumes({ limit: 50 })`（按登录态返回），删除走 DELETE /api/v1/resumes/{id}
 * - 订单列表：后端还没有「按登录用户返回」的接口，这里只给明确入口，不造假数据。
 */

type Section = 'profile' | 'resumes' | 'orders' | 'plan';

const SECTIONS: Array<{ key: Section; label: string; desc: string }> = [
  { key: 'profile', label: '账号信息', desc: '邮箱、手机号与安全设置' },
  { key: 'resumes', label: '我的简历', desc: '已保存的简历，可继续编辑或删除' },
  { key: 'orders', label: '订单记录', desc: '支付记录与发票信息' },
  { key: 'plan', label: '订阅方案', desc: '当前套餐与用量' },
];

type ResumesStatus = 'idle' | 'loading' | 'ready' | 'error';

export function AccountCenter() {
  const router = useRouter();
  const [session, setSession] = React.useState<AuthSession | null>(null);
  const [summary, setSummary] = React.useState<AccountSummary | null>(null);
  const [ready, setReady] = React.useState(false);
  const [section, setSection] = React.useState<Section>('profile');

  // 改邮箱
  const [emailDraft, setEmailDraft] = React.useState('');
  const [notice, setNotice] = React.useState('');
  const [noticeError, setNoticeError] = React.useState(false);

  // 改密码
  const [oldPassword, setOldPassword] = React.useState('');
  const [newPassword, setNewPassword] = React.useState('');
  const [confirmPassword, setConfirmPassword] = React.useState('');
  const [pwdNotice, setPwdNotice] = React.useState('');
  const [pwdError, setPwdError] = React.useState(false);

  const [saving, setSaving] = React.useState(false);

  // 我的简历
  const [resumes, setResumes] = React.useState<ResumeSummary[]>([]);
  const [resumesStatus, setResumesStatus] = React.useState<ResumesStatus>('idle');
  const [resumesError, setResumesError] = React.useState('');
  const [deletingResumeId, setDeletingResumeId] = React.useState<string | null>(null);

  React.useEffect(() => {
    const current = authApi.getSession();
    if (!current) {
      // 未登录 → 去登录页，登录后回到这里
      router.replace('/login?next=' + encodeURIComponent('/user'));
      return;
    }
    setSession(current);
    setEmailDraft(current.account.email || '');
    setReady(true);

    // 拉一次服务端账号摘要；401 会由 lib/api.ts 统一跳登录
    authApi
      .me()
      .then((data) => {
        setSummary(data);
        setSession(authApi.getSession());
        setEmailDraft(data.account.email || '');
      })
      .catch(() => {
        /* 后端不可达时保留本地缓存展示，不阻断页面 */
      });
  }, [router]);

  /** 拉取当前账号名下的简历（登录态由 lib/api.ts 自动带上 Bearer / X-Device-Id）。 */
  const loadResumes = React.useCallback(async () => {
    setResumesStatus('loading');
    setResumesError('');
    try {
      const data = await listResumes({ limit: 50 });
      setResumes(data.items);
      setResumesStatus('ready');
    } catch (e) {
      setResumesError(e instanceof Error ? e.message : '简历列表加载失败，请稍后重试');
      setResumesStatus('error');
    }
  }, []);

  // 每次切到「我的简历」tab 都重新拉一次，保证在编辑器里新保存的简历能立刻看到。
  React.useEffect(() => {
    if (section !== 'resumes') return;
    void loadResumes();
  }, [loadResumes, section]);

  const onDeleteResume = async (item: ResumeSummary) => {
    if (deletingResumeId) return;
    if (!window.confirm(`确定删除《${item.title || '未命名简历'}》吗？删除后不可恢复。`)) return;
    setDeletingResumeId(item.resume_id);
    setResumesError('');
    try {
      await deleteResume(item.resume_id);
      // 本地先移除，列表立即反馈；下次进入 tab 会重新拉取
      setResumes((prev) => prev.filter((resume) => resume.resume_id !== item.resume_id));
    } catch (e) {
      setResumesError(e instanceof Error ? e.message : '删除失败，请稍后重试');
    } finally {
      setDeletingResumeId(null);
    }
  };

  const onSaveEmail = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || !session) return;
    setSaving(true);
    setNotice('');
    try {
      const next = await authApi.updateEmail(emailDraft.trim());
      setSession(next);
      setNotice('邮箱已更新');
      setNoticeError(false);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '保存失败');
      setNoticeError(true);
    } finally {
      setSaving(false);
    }
  };

  const onChangePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setPwdNotice('');
    try {
      const next = await authApi.changePassword({
        oldPassword: session?.account.autoCreated ? undefined : oldPassword,
        newPassword,
        confirmPassword,
      });
      setSession(next);
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setPwdNotice('密码已更新，其它设备需要重新登录');
      setPwdError(false);
    } catch (e) {
      setPwdNotice(e instanceof Error ? e.message : '修改失败');
      setPwdError(true);
    } finally {
      setSaving(false);
    }
  };

  if (!ready || !session) {
    return (
      <div className="py-24 text-center text-sm text-ink-500" role="status">
        正在读取账号信息…
      </div>
    );
  }

  const { account } = session;
  const display = displayName(account);

  /** 退出登录：先通知后端拉黑 jti，再回首页 */
  const logout = () => {
    void authApi.logout().finally(() => router.push('/'));
  };

  return (
    <div className="grid gap-10 lg:grid-cols-[220px_minmax(0,1fr)]">
      {/* 左侧导航 */}
      <nav aria-label="账号中心" className="flex flex-col gap-1">
        {SECTIONS.map((item) => {
          const active = section === item.key;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setSection(item.key)}
              aria-current={active ? 'page' : undefined}
              className={
                'border-l-2 px-3 py-2.5 text-left text-[13px] font-medium transition-colors duration-200 ' +
                (active
                  ? 'border-ink-950 text-ink-950'
                  : 'border-transparent text-ink-400 hover:border-ink-300 hover:text-ink-950')
              }
            >
              {item.label}
            </button>
          );
        })}
        <button
          type="button"
          onClick={logout}
          className="mt-4 border-l-2 border-transparent px-3 py-2.5 text-left text-[13px] font-medium text-ink-400 transition-colors duration-200 hover:text-red-700"
        >
          退出登录
        </button>
      </nav>

      {/* 右侧内容 */}
      <section aria-live="polite">
        <header className="mb-6 border-b border-ink-200 pb-4">
          <h1 className="text-[22px] font-bold tracking-tight text-ink-950">
            {SECTIONS.find((s) => s.key === section)?.label}
          </h1>
          <p className="mt-1 text-[13px] text-ink-600">{SECTIONS.find((s) => s.key === section)?.desc}</p>
        </header>

        {section === 'profile' ? (
          <div className="grid gap-5">
            {account.autoCreated ? (
              <div className="border border-brand-300 bg-brand-50/60 p-4 text-[13px] leading-relaxed text-ink-700">
                这个账号由「手机验证码首次登录」自动创建，邮箱与密码都是系统生成的占位值，
                <strong className="text-ink-950">并不是你设置的</strong>。建议改成自己的邮箱与密码。
              </div>
            ) : null}

            <dl className="grid gap-4 border border-ink-200 bg-white p-5 sm:grid-cols-2">
              <Detail label="手机号" value={account.phone || '—'} mono />
              <Detail label="邮箱" value={account.email || '—'} mono />
              <Detail label="展示名" value={display} />
              <Detail
                label="注册时间"
                value={account.createdAt ? new Date(account.createdAt).toLocaleString('zh-CN') : '—'}
              />
            </dl>

            {/* 额度：来自 GET /auth/me */}
            <section className="border border-ink-200 bg-white p-5">
              <h2 className="text-[13px] font-semibold text-ink-950">诊断额度</h2>
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink-600">
                本账号已使用 <span className="font-mono text-ink-950">{summary?.quota.used ?? account.freeQuotaUsed}</span>
                {summary ? ` / ${summary.quota.free}` : ''} 次免费诊断
                {summary && !summary.quota.enforced ? '（当前未启用额度限制）' : ''}。
                {summary?.hasPaid ? '已购买过套餐。' : '暂未购买套餐。'}
              </p>
            </section>

            {/* 改邮箱 */}
            <form
              onSubmit={onSaveEmail}
              className="grid gap-3 border border-ink-200 bg-white p-5"
              aria-label="修改邮箱"
            >
              <label htmlFor="profile-email" className="text-[13px] font-semibold text-ink-950">
                修改邮箱
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <input
                  id="profile-email"
                  type="email"
                  value={emailDraft}
                  onChange={(e) => setEmailDraft(e.target.value)}
                  disabled={saving}
                  placeholder="you@example.com"
                  className="h-9 w-full max-w-sm border border-ink-300 px-3 text-[13px] focus:border-ink-950 focus:outline-none disabled:bg-ink-100"
                />
                <button
                  type="submit"
                  disabled={saving || !emailDraft || emailDraft === account.email}
                  className="h-9 border border-ink-950 bg-ink-950 px-4 text-[12px] font-semibold text-white transition-opacity disabled:opacity-40"
                >
                  {saving ? '保存中…' : '保存'}
                </button>
              </div>
              {notice ? <p className={`text-[12px] ${noticeError ? 'text-red-700' : 'text-green-700'}`}>{notice}</p> : null}
            </form>

            {/* 改密码 */}
            <form onSubmit={onChangePassword} className="grid gap-3 border border-ink-200 bg-white p-5" aria-label="修改密码">
              <p className="text-[13px] font-semibold text-ink-950">修改密码</p>
              <p className="text-[12px] leading-relaxed text-ink-500">
                {account.autoCreated
                  ? '自动建号的账号没有旧密码，直接设置新密码即可。'
                  : '修改后其它设备上的登录会立即失效。'}
              </p>
              <div className="grid gap-3 sm:grid-cols-3">
                {!account.autoCreated ? (
                  <PasswordInput label="旧密码" value={oldPassword} onChange={setOldPassword} disabled={saving} autoComplete="current-password" />
                ) : null}
                <PasswordInput label="新密码" value={newPassword} onChange={setNewPassword} disabled={saving} autoComplete="new-password" />
                <PasswordInput label="确认新密码" value={confirmPassword} onChange={setConfirmPassword} disabled={saving} autoComplete="new-password" />
              </div>
              <div>
                <button
                  type="submit"
                  disabled={saving || !newPassword || !confirmPassword}
                  className="h-9 border border-ink-950 bg-ink-950 px-4 text-[12px] font-semibold text-white transition-opacity disabled:opacity-40"
                >
                  {saving ? '提交中…' : '修改密码'}
                </button>
              </div>
              {pwdNotice ? (
                <p className={`text-[12px] ${pwdError ? 'text-red-700' : 'text-green-700'}`}>{pwdNotice}</p>
              ) : null}
            </form>
          </div>
        ) : null}
        {section === 'resumes' ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-[12px] leading-relaxed text-ink-600">
                这里列出当前账号名下的简历（上传解析的与编辑器保存的）。
                编辑器保存过的简历可以「继续编辑」，改完再发起诊断。
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void loadResumes()}
                  disabled={resumesStatus === 'loading'}
                  className="h-8 border border-ink-300 px-3 text-[12px] font-semibold text-ink-700 transition-colors hover:border-ink-950 disabled:opacity-40"
                >
                  刷新
                </button>
                <Link
                  href="/editor"
                  className="inline-flex h-8 items-center border border-ink-950 bg-ink-950 px-3 text-[12px] font-semibold text-white transition-colors hover:border-brand-500 hover:bg-brand-500"
                >
                  新建简历
                </Link>
              </div>
            </div>

            {resumesStatus === 'loading' || resumesStatus === 'idle' ? (
              <div className="space-y-2" aria-busy="true">
                {[0, 1, 2].map((index) => (
                  <div key={index} className="h-14 animate-pulse border border-ink-200 bg-ink-50" />
                ))}
                <span className="sr-only">简历列表加载中…</span>
              </div>
            ) : null}

            {resumesStatus === 'error' ? (
              <div className="border border-dashed border-red-300 px-6 py-10 text-center">
                <p className="text-[13px] font-semibold text-ink-950">简历列表加载失败</p>
                <p className="mx-auto mt-1 max-w-md text-[12px] leading-relaxed text-ink-600">
                  {resumesError || '请稍后重试'}
                </p>
                <button
                  type="button"
                  onClick={() => void loadResumes()}
                  className="mt-4 h-8 border border-ink-950 px-3 text-[12px] font-semibold text-ink-950 transition-colors hover:bg-ink-950 hover:text-white"
                >
                  重试
                </button>
              </div>
            ) : null}

            {resumesStatus === 'ready' ? (
              resumes.length > 0 ? (
                <ul className="divide-y divide-ink-200 border-y border-ink-200">
                  {resumes.map((item) => (
                    <li
                      key={item.resume_id}
                      className="flex flex-wrap items-center justify-between gap-3 py-3"
                    >
                      <div className="min-w-0">
                        <p className="flex items-center gap-2">
                          <span className="truncate text-[13px] font-semibold text-ink-950">
                            {item.title || '未命名简历'}
                          </span>
                          <span
                            className={
                              item.source === 'editor'
                                ? 'shrink-0 bg-ink-950 px-1.5 py-0.5 text-[10.5px] font-semibold text-white'
                                : 'shrink-0 bg-ink-100 px-1.5 py-0.5 text-[10.5px] font-medium text-ink-600'
                            }
                          >
                            {item.source === 'editor' ? '编辑器' : '上传'}
                          </span>
                        </p>
                        <p className="mt-1 text-[11.5px] leading-relaxed text-ink-500">
                          {[item.parsed_name, item.parsed_title].filter(Boolean).join(' · ') ||
                            '暂无解析信息'}
                          <span className="text-ink-400"> · 创建于 {formatDate(item.created_at)}</span>
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Link
                          href={`/editor/write?start=resume&resumeId=${encodeURIComponent(item.resume_id)}`}
                          className="inline-flex h-8 items-center border border-ink-950 px-3 text-[12px] font-semibold text-ink-950 transition-colors hover:bg-ink-950 hover:text-white"
                        >
                          继续编辑
                        </Link>
                        <button
                          type="button"
                          onClick={() => void onDeleteResume(item)}
                          disabled={deletingResumeId === item.resume_id}
                          className="h-8 border border-transparent px-2 text-[12px] font-semibold text-red-700 transition-colors hover:border-red-200 hover:bg-red-50 disabled:opacity-40"
                        >
                          {deletingResumeId === item.resume_id ? '删除中…' : '删除'}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="还没有保存过简历"
                  desc="在简历编辑器里写完内容后点「保存到账号」，或先上传一份现有简历；这里会列出你的简历，可以继续编辑或删除。"
                  action={{ href: '/editor', label: '新建简历' }}
                />
              )
            ) : null}
          </div>
        ) : null}

        {section === 'orders' ? (
          <EmptyState
            title="暂无订单"
            desc="订单接口（`GET /api/v1/payments/orders`）接入登录态后，这里会列出支付记录。"
            action={{ href: '/pricing', label: '查看定价' }}
          />
        ) : null}

        {section === 'plan' ? (
          <EmptyState
            title="当前：免费版"
            desc="免费版可直接使用简历诊断。升级套餐后可在订单记录里查看支付状态。"
            action={{ href: '/pricing', label: '查看订阅方案' }}
          />
        ) : null}
      </section>
    </div>
  );
}

/** 时间戳展示：无效日期直接回原文，避免出现 "Invalid Date"。 */
function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-500">{label}</dt>
      <dd className={(mono ? 'font-mono ' : '') + 'mt-1 break-all text-[13px] text-ink-950'}>{value}</dd>
    </div>
  );
}

/** 密码输入框：标签 + 输入框，三栏并排用 */
function PasswordInput({
  label,
  value,
  onChange,
  disabled,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  autoComplete?: string;
}) {
  const id = `pwd-${label}`;
  return (
    <label className="grid gap-1.5" htmlFor={id}>
      <span className="text-[12px] text-ink-600">{label}</span>
      <input
        id={id}
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        autoComplete={autoComplete}
        className="h-9 border border-ink-300 px-3 text-[13px] focus:border-ink-950 focus:outline-none disabled:bg-ink-100"
      />
    </label>
  );
}
function EmptyState({
  title,
  desc,
  action,
}: {
  title: string;
  desc: string;
  action: { href: string; label: string };
}) {
  return (
    <div className="border border-dashed border-ink-300 px-6 py-14 text-center">
      <p className="text-[15px] font-semibold text-ink-950">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-ink-600">{desc}</p>
      <Link
        href={action.href}
        className="mt-5 inline-flex items-center border border-ink-950 px-3 py-1.5 text-[12px] font-semibold text-ink-950 transition-colors hover:bg-ink-950 hover:text-white"
      >
        {action.label}
      </Link>
    </div>
  );
}

export default AccountCenter;
