'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { SMS_RESEND_SECONDS, PATTERNS, authApi, type AuthSession } from '@/lib/auth';
import styles from '@/app/login/login.module.css';

/**
 * 登录 / 创建账号卡片
 * ------------------------------------------------------------------
 * 视觉与动效来源：login-preview 原型。
 *
 * 三种入口：邮箱密码登录 / 手机验证码登录（无账号自动建号）/ 创建账号。
 *
 * 成功时间轴（需求指定）：
 *   1. 校验通过 → 「先隐藏卡片」：卡片立即淡出 + 禁用交互（cardLeaving）
 *   2. WELCOME 字身与背景同步 1000ms 过渡：灰白 → 紫色，cubic-bezier(0.16,1,0.3,1)
 *   3. 过渡完成后停留 500ms
 *   4. 再转场到首页（router.replace）
 *
 * 失败：卡片保持可见，错误条展开 + 对应字段红边线，可重新提交。
 *
 * 注意：页面级状态（data-phase / data-done）挂在 <section> 根节点上，
 * 卡片隐藏**不会卸载**整个组件，所以背景过渡不会被打断。
 */

type Step = 'password' | 'sms' | 'register';
type Phase = 'idle' | 'leaving' | 'hold' | 'to-home';

/** 与 CSS 变量 --auth-t / --auth-hold / --card-out 保持一致 */
const T_AUTH = 1000;
const T_HOLD = 500;
/** 卡片淡出时长（与 --card-out 一致）：卡片完全消失后才开始背景变色 */
const T_CARD_OUT = 320;
/** 转场到首页前留给页面淡出的时间（与 --to-home 呼应） */
const T_NAVIGATE = 320;

const STEP_META: Record<Step, { label: string; loading: string; title: string; footer: 'login' | 'register' }> = {
  password: { label: '登录', loading: '验证中…', title: '', footer: 'login' },
  sms: { label: '登录', loading: '验证中…', title: '', footer: 'login' },
  register: { label: '创建账号', loading: '创建中…', title: '创建账号', footer: 'register' },
};

interface LoginCardProps {
  /** 登录成功后要去的地方；缺省回首页 */
  nextPath?: string;
  /** 登录成功后的回调（可用于刷新全局登录态） */
  onSuccess?: (session: AuthSession) => void;
}

export function LoginCard({ nextPath, onSuccess }: LoginCardProps) {
  const router = useRouter();
  const [step, setStep] = React.useState<Step>('password');
  const [tab, setTab] = React.useState<'password' | 'sms'>('password');
  const [phase, setPhase] = React.useState<Phase>('idle');
  /** WELCOME 收成紫色水印：由 CSS 的 animation（不是 transition）接管，避免回弹 */
  const [done, setDone] = React.useState(false);
  const [cardIn, setCardIn] = React.useState(false);
  const [cardLeaving, setCardLeaving] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; field?: string } | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  // 登录表单
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [showPassword, setShowPassword] = React.useState(false);
  const [phone, setPhone] = React.useState('');
  const [code, setCode] = React.useState('');
  const [demoCode, setDemoCode] = React.useState('');
  const [countdown, setCountdown] = React.useState(0);

  // 创建账号表单
  const [regEmail, setRegEmail] = React.useState('');
  const [regPhone, setRegPhone] = React.useState('');
  const [regCode, setRegCode] = React.useState('');
  const [regPassword, setRegPassword] = React.useState('');
  const [regConfirm, setRegConfirm] = React.useState('');
  const [showRegPassword, setShowRegPassword] = React.useState(false);
  const [regDemoCode, setRegDemoCode] = React.useState('');
  const [regCountdown, setRegCountdown] = React.useState(0);

  const timers = React.useRef<ReturnType<typeof setTimeout>[]>([]);
  const later = React.useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(fn, ms);
    timers.current.push(id);
    return id;
  }, []);

  React.useEffect(
    () => () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    },
    [],
  );

  // 入场：下一帧加 is-in，保证 opacity/transform 过渡能播放
  React.useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setCardIn(true)));
    return () => cancelAnimationFrame(id);
  }, []);

  // 验证码倒计时
  React.useEffect(() => {
    if (countdown <= 0) return undefined;
    const id = setTimeout(() => setCountdown((v) => v - 1), 1000);
    return () => clearTimeout(id);
  }, [countdown]);

  React.useEffect(() => {
    if (regCountdown <= 0) return undefined;
    const id = setTimeout(() => setRegCountdown((v) => v - 1), 1000);
    return () => clearTimeout(id);
  }, [regCountdown]);

  const clearError = () => setError(null);
  const fail = (message: string, field?: string) => setError({ message, field });

  const switchStep = (next: Step) => {
    setStep(next);
    setError(null);
    setNotice(null);
    if (next !== 'register') setTab(next === 'sms' ? 'sms' : 'password');
  };

  /**
   * 登录成功后的时间轴（与 login-preview 原型一致）：
   *   ① 卡片先完全消失（淡出 320ms），此时背景仍是灰白
   *   ② 卡片消失后，背景 + WELCOME 才开始 1000ms 变紫
   *   ③ 变色完成后停留 500ms
   *   ④ 再转场到首页
   */
  const finish = (session: AuthSession) => {
    onSuccess?.(session);
    setNotice(null);

    // ① 先让整张卡片消失：淡出 + 停掉所有交互（背景保持灰白）
    setCardLeaving(true);

    later(() => {
      // ② 卡片已经看不见了，才开始背景/WELCOME 的 1000ms 过渡
      setPhase('leaving');

      later(() => {
        // ③ 过渡完成 → 停留 500ms
        setPhase('hold');
        setDone(true);

        later(() => {
          // ④ 转场首页
          setPhase('to-home');
          later(() => router.replace(nextPath || '/'), T_NAVIGATE);
        }, T_HOLD);
      }, T_AUTH);
    }, T_CARD_OUT);
  };

  /* ---------------- 验证码 ---------------- */

  const sendCode = async (purpose: 'login' | 'register') => {
    clearError();
    const target = purpose === 'login' ? phone.trim() : regPhone.trim();
    if (!PATTERNS.phone.test(target)) {
      fail('请输入正确的 11 位手机号', purpose === 'login' ? 'field-phone' : 'field-reg-phone');
      return;
    }
    try {
      const res = await authApi.sendSmsCode(target, purpose);
      // 开发模式（后端 SMS_DEV_MODE=true）会把验证码回传，方便本地联调；生产模式为 null
      if (purpose === 'login') {
        setDemoCode(res.devCode || '');
        setCountdown(SMS_RESEND_SECONDS);
      } else {
        setRegDemoCode(res.devCode || '');
        setRegCountdown(SMS_RESEND_SECONDS);
      }
    } catch (e) {
      fail(e instanceof Error ? e.message : '验证码发送失败', purpose === 'login' ? 'field-phone' : 'field-reg-phone');
    }
  };

  /* ---------------- 提交 ---------------- */

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (loading || cardLeaving) return; // 防重复提交
    clearError();
    setNotice(null);

    // 创建账号：五栏全部必填，命中即停，只标红一栏
    if (step === 'register') {
      if (!PATTERNS.email.test(regEmail.trim())) return fail('请输入正确的邮箱地址', 'field-reg-email');
      if (!PATTERNS.phone.test(regPhone.trim())) return fail('请输入正确的 11 位手机号', 'field-reg-phone');
      if (regCode.trim().length !== 6) return fail('请输入 6 位手机验证码', 'field-reg-code');
      if (!PATTERNS.password.test(regPassword)) {
        return fail('密码至少 8 位，且同时包含字母和数字', 'field-reg-password');
      }
      if (regConfirm !== regPassword) return fail('两次输入的密码不一致', 'field-reg-confirm');

      setLoading(true);
      try {
        const session = await authApi.register({
          email: regEmail.trim(),
          phone: regPhone.trim(),
          code: regCode.trim(),
          password: regPassword,
        });
        finish(session);
      } catch (e) {
        setLoading(false);
        fail(e instanceof Error ? e.message : '创建失败，请稍后重试', 'field-reg-email');
      }
      return undefined;
    }

    setLoading(true);
    try {
      if (step === 'password') {
        if (!PATTERNS.email.test(email.trim())) {
          setLoading(false);
          return fail('请输入正确的邮箱地址', 'field-email');
        }
        if (!password) {
          setLoading(false);
          return fail('请输入密码', 'field-password');
        }
        const session = await authApi.loginEmail(email, password);
        finish(session);
      } else {
        if (!PATTERNS.phone.test(phone.trim())) {
          setLoading(false);
          return fail('请输入正确的 11 位手机号', 'field-phone');
        }
        if (code.trim().length !== 6) {
          setLoading(false);
          return fail('请输入 6 位验证码', 'field-code');
        }
        const { session } = await authApi.loginSms(phone.trim(), code.trim());
        finish(session);
      }
    } catch (e) {
      // 失败：卡片保持可见，错误提示展开，输入框恢复可编辑
      setLoading(false);
      const message = e instanceof Error ? e.message : '登录失败，请稍后重试';
      setNotice('登录失败：' + message);
      fail(message, step === 'sms' ? 'field-code' : 'field-password');
    }
    return undefined;
  };

  /* ---------------- 渲染辅助 ---------------- */

  const fieldClass = (id: string) => `${styles.field} ${error?.field === id ? styles['has-error'] : ''}`;
  const paneClass = (id: Step) => `${styles.auth__pane} ${step === id ? `${styles['is-active']}` : ''}`;
  const digits = (value: string, max: number) => value.replace(/\D/g, '').slice(0, max);
  const locked = loading || cardLeaving;

  return (
    <section
      className={styles.root}
      data-phase={phase}
      data-done={done ? 'true' : 'false'}
      data-tab={tab}
      data-step={step}
      aria-label={step === 'register' ? '创建账号' : '登录'}
    >
      {/* 背景大标题：成功时由灰白过渡到紫色 */}
      <div className={`${styles.auth__welcome} ${cardIn ? styles['is-in'] : ''}`} aria-hidden="true">
        <span className={styles['auth__welcome-item']} data-text="WELCOME">
          WELCOME
        </span>
        <span className={styles['auth__welcome-item']} data-text="WELCOME">
          WELCOME
        </span>
        <span className={styles['auth__welcome-item']} data-text="WELCOME">
          WELCOME
        </span>
      </div>

      {/* 页面级提示：放在卡片外面，卡片隐藏后仍然可见 */}
      {notice ? (
        <p className={styles['auth__notice']} role="status">
          {notice}
        </p>
      ) : null}

      <div className={styles.auth__stage}>
        <div
          className={`${styles.auth__card} ${cardIn ? styles['is-in'] : ''} ${cardLeaving ? styles['is-out'] : ''}`}
          aria-busy={loading}
        >
          <h1 className={styles.logo}>
            <span className={styles.logo__mark} aria-hidden="true">
              简
            </span>
            <span className={styles.logo__wordmark}>
              <span className={styles.logo__zh}>简甄</span>
              <span className={styles.logo__en}>ZHENRESUME</span>
            </span>
          </h1>
          <p className={styles.auth__subtitle}>简阅简历 · 甄识良才</p>

          {/* 登录方式切换：创建账号屏隐藏 */}
          <div className={styles.auth__tabs} role="tablist" aria-label="登录方式" hidden={step === 'register'}>
            <span className={styles['auth__tabs-thumb']} aria-hidden="true" />
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'password'}
              aria-controls="pane-password"
              className={styles.auth__tab}
              onClick={() => {
                if (locked || tab === 'password') return;
                switchStep('password');
              }}
            >
              邮箱密码
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'sms'}
              aria-controls="pane-sms"
              className={styles.auth__tab}
              onClick={() => {
                if (locked || tab === 'sms') return;
                switchStep('sms');
              }}
            >
              手机验证码
            </button>
          </div>

          <p className={styles['auth__step-title']}>{STEP_META[step].title}</p>

          <form className={styles.auth__form} onSubmit={onSubmit} noValidate>
            {/* ============ 邮箱 + 密码 ============ */}
            <div id="pane-password" role="tabpanel" hidden={step !== 'password'} className={paneClass('password')}>
              <div className={fieldClass('field-email')}>
                <label className={styles.field__label} htmlFor="email">
                  邮箱
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="3.5" y="5.5" width="17" height="13" rx="2.6" />
                    <path d="m4.4 7.4 6.5 4.7a2 2 0 0 0 2.2 0l6.5-4.7" />
                  </svg>
                  <input
                    id="email"
                    className={styles.field__input}
                    type="email"
                    placeholder="you@example.com"
                    autoComplete="email"
                    spellCheck={false}
                    disabled={locked}
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      clearError();
                    }}
                  />
                </div>
              </div>

              <div className={fieldClass('field-password')}>
                <label className={styles.field__label} htmlFor="password">
                  密码
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="5" y="10.5" width="14" height="9.5" rx="2.6" />
                    <path d="M8.6 10.5V8a3.4 3.4 0 0 1 6.8 0v2.5" />
                  </svg>
                  <input
                    id="password"
                    className={styles.field__input}
                    type={showPassword ? 'text' : 'password'}
                    placeholder="请输入密码"
                    autoComplete="current-password"
                    disabled={locked}
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      clearError();
                    }}
                  />
                  <button
                    type="button"
                    className={styles.field__toggle}
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                    onClick={() => setShowPassword((v) => !v)}
                  >
                    {showPassword ? '隐藏' : '显示'}
                  </button>
                </div>
              </div>
            </div>

            {/* ============ 手机号 + 验证码 ============ */}
            <div id="pane-sms" role="tabpanel" hidden={step !== 'sms'} className={paneClass('sms')}>
              <div className={fieldClass('field-phone')}>
                <label className={styles.field__label} htmlFor="phone">
                  手机号
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="6.5" y="2.8" width="11" height="18.4" rx="2.6" />
                    <path d="M10.6 18.2h2.8" />
                  </svg>
                  <input
                    id="phone"
                    className={styles.field__input}
                    type="tel"
                    inputMode="numeric"
                    maxLength={11}
                    placeholder="11 位手机号"
                    autoComplete="tel-national"
                    disabled={locked}
                    value={phone}
                    onChange={(e) => {
                      setPhone(digits(e.target.value, 11));
                      clearError();
                    }}
                  />
                </div>
              </div>

              <div className={fieldClass('field-code')}>
                <label className={styles.field__label} htmlFor="code">
                  验证码
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M3.8 6.6h16.4v10.8H3.8z" />
                    <path d="M8 10.4h2.4M8 13.6h.1M12 10.4h.1M12 13.6h4" />
                  </svg>
                  <input
                    id="code"
                    className={styles.field__input}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="6 位验证码"
                    autoComplete="one-time-code"
                    disabled={locked}
                    value={code}
                    onChange={(e) => {
                      setCode(digits(e.target.value, 6));
                      clearError();
                    }}
                  />
                  <button
                    type="button"
                    className={styles.field__send}
                    disabled={locked || countdown > 0}
                    onClick={() => sendCode('login')}
                  >
                    {countdown > 0 ? `${countdown}s 后重发` : '获取验证码'}
                  </button>
                </div>
              </div>

              {/* 模拟短信：接真实短信后删除（后端不应把验证码返回前端） */}
              {demoCode ? (
                <p className={styles['auth__demo-code']}>
                  <span className={styles['auth__demo-tag']}>模拟短信</span>
                  <span>
                    验证码 <strong>{demoCode}</strong>，5 分钟内有效
                  </span>
                </p>
              ) : null}
            </div>

            {/* ============ 创建账号 ============ */}
            <div id="pane-register" role="tabpanel" hidden={step !== 'register'} className={paneClass('register')}>
              <div className={fieldClass('field-reg-email')}>
                <label className={styles.field__label} htmlFor="reg-email">
                  邮箱<i className={styles.field__req}>必填</i>
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="3.5" y="5.5" width="17" height="13" rx="2.6" />
                    <path d="m4.4 7.4 6.5 4.7a2 2 0 0 0 2.2 0l6.5-4.7" />
                  </svg>
                  <input
                    id="reg-email"
                    className={styles.field__input}
                    type="email"
                    placeholder="you@example.com"
                    autoComplete="email"
                    spellCheck={false}
                    disabled={locked}
                    value={regEmail}
                    onChange={(e) => {
                      setRegEmail(e.target.value);
                      clearError();
                    }}
                  />
                </div>
              </div>

              <div className={fieldClass('field-reg-phone')}>
                <label className={styles.field__label} htmlFor="reg-phone">
                  手机号<i className={styles.field__req}>必填</i>
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="6.5" y="2.8" width="11" height="18.4" rx="2.6" />
                    <path d="M10.6 18.2h2.8" />
                  </svg>
                  <input
                    id="reg-phone"
                    className={styles.field__input}
                    type="tel"
                    inputMode="numeric"
                    maxLength={11}
                    placeholder="11 位手机号"
                    autoComplete="tel-national"
                    disabled={locked}
                    value={regPhone}
                    onChange={(e) => {
                      setRegPhone(digits(e.target.value, 11));
                      clearError();
                    }}
                  />
                </div>
              </div>

              <div className={fieldClass('field-reg-code')}>
                <label className={styles.field__label} htmlFor="reg-code">
                  手机验证码<i className={styles.field__req}>必填</i>
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M3.8 6.6h16.4v10.8H3.8z" />
                    <path d="M8 10.4h2.4M8 13.6h.1M12 10.4h.1M12 13.6h4" />
                  </svg>
                  <input
                    id="reg-code"
                    className={styles.field__input}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="6 位验证码"
                    autoComplete="one-time-code"
                    disabled={locked}
                    value={regCode}
                    onChange={(e) => {
                      setRegCode(digits(e.target.value, 6));
                      clearError();
                    }}
                  />
                  <button
                    type="button"
                    className={styles.field__send}
                    disabled={locked || regCountdown > 0}
                    onClick={() => sendCode('register')}
                  >
                    {regCountdown > 0 ? `${regCountdown}s 后重发` : '获取验证码'}
                  </button>
                </div>
              </div>

              <div className={fieldClass('field-reg-password')}>
                <label className={styles.field__label} htmlFor="reg-password">
                  密码<i className={styles.field__req}>必填</i>
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="5" y="10.5" width="14" height="9.5" rx="2.6" />
                    <path d="M8.6 10.5V8a3.4 3.4 0 0 1 6.8 0v2.5" />
                  </svg>
                  <input
                    id="reg-password"
                    className={styles.field__input}
                    type={showRegPassword ? 'text' : 'password'}
                    placeholder="至少 8 位，含字母和数字"
                    autoComplete="new-password"
                    disabled={locked}
                    value={regPassword}
                    onChange={(e) => {
                      setRegPassword(e.target.value);
                      clearError();
                    }}
                  />
                  <button
                    type="button"
                    className={styles.field__toggle}
                    aria-label={showRegPassword ? '隐藏密码' : '显示密码'}
                    onClick={() => setShowRegPassword((v) => !v)}
                  >
                    {showRegPassword ? '隐藏' : '显示'}
                  </button>
                </div>
              </div>

              <div className={fieldClass('field-reg-confirm')}>
                <label className={styles.field__label} htmlFor="reg-confirm">
                  确认密码<i className={styles.field__req}>必填</i>
                </label>
                <div className={styles.field__control}>
                  <svg className={styles.field__icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="5" y="10.5" width="14" height="9.5" rx="2.6" />
                    <path d="M9 15.4l2 2 4-4.2" />
                  </svg>
                  <input
                    id="reg-confirm"
                    className={styles.field__input}
                    type="password"
                    placeholder="再输入一次密码"
                    autoComplete="new-password"
                    disabled={locked}
                    value={regConfirm}
                    onChange={(e) => {
                      setRegConfirm(e.target.value);
                      clearError();
                    }}
                  />
                </div>
              </div>

              {regDemoCode ? (
                <p className={styles['auth__demo-code']}>
                  <span className={styles['auth__demo-tag']}>模拟短信</span>
                  <span>
                    验证码 <strong>{regDemoCode}</strong>，5 分钟内有效
                  </span>
                </p>
              ) : null}
            </div>

            {/* 错误提示：失败时展开；登录成功后卡片整体隐藏，这里自然不可见 */}
            <p className={`${styles.auth__error} ${error ? styles['is-visible'] : ''}`} role="alert" aria-live="assertive">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
                <circle cx="12" cy="12" r="8.6" />
                <path d="M12 8.2v4.6M12 15.6v.1" />
              </svg>
              <span>{error?.message || ''}</span>
            </p>

            <button
              className={styles.auth__submit}
              type="submit"
              data-loading={loading}
              disabled={locked}
              aria-busy={loading}
            >
              <span className={styles['auth__submit-label']}>{STEP_META[step].label}</span>
              <span className={styles['auth__submit-loader']} aria-hidden="true">
                <i className={styles.spinner} />
                <span>{STEP_META[step].loading}</span>
              </span>
            </button>
          </form>

          {/* 底部文字跳转 */}
          <div className={styles.auth__footer} hidden={STEP_META[step].footer !== 'login'}>
            <span>还没有账号？</span>
            <button
              className={styles.auth__link}
              type="button"
              disabled={locked}
              onClick={() => {
                if (!regPhone) setRegPhone(phone);
                switchStep('register');
              }}
            >
              创建账号
            </button>
          </div>

          <div className={styles.auth__footer} hidden={STEP_META[step].footer !== 'register'}>
            <span>已有账号？</span>
            <button className={styles.auth__link} type="button" disabled={locked} onClick={() => switchStep('password')}>
              去登录
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

export default LoginCard;
