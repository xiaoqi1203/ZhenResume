/**
 * 账号与登录态（前端侧）
 * ------------------------------------------------------------------
 * 对接后端 `/api/v1/auth/*`（backend/app/api/auth.py）：
 *   POST   /auth/sms-code        发送短信验证码 { phone, purpose }
 *   POST   /auth/register        创建账号 { email, phone, code, password }
 *   POST   /auth/login           邮箱 + 密码
 *   POST   /auth/login-sms       手机验证码登录（手机号不存在时后端自动建号）
 *   GET    /auth/me              当前账号 + 额度
 *   PATCH  /auth/me              改邮箱
 *   POST   /auth/change-password 改密码（旧 token 全部失效）
 *   POST   /auth/logout          登出（jti 进黑名单）
 *
 * 约定：
 * - token 存 localStorage 的 `auth.token`（`lib/api.ts` 直接读它挂 Authorization）；
 * - `auth.session` 只存**展示用**账号信息，不存密码；
 * - 登录/注册/验证码登录会附带 `X-Device-Id`（localStorage `zhenresume.device_id`），
 *   后端据此把匿名期间产生的数据认领到账号；
 * - 校验规则（手机号 / 邮箱 / 密码）与后端 services/security.py 保持一致，
 *   前端先拦一遍是为了即时反馈，最终以后端结果为准。
 */

export type SmsPurpose = 'login' | 'register';

export interface AuthAccount {
  userId: string;
  phone: string | null;
  email: string | null;
  role: string;
  /** 邮箱/密码是否由「手机验证码首次登录」自动生成（占位乱码） */
  autoCreated: boolean;
  freeQuotaUsed: number;
  createdAt: string | null;
}

export interface AuthSession {
  account: AuthAccount;
  /** 展示名：邮箱登录显示脱敏邮箱，验证码登录显示脱敏手机号 */
  display: string;
}

export interface AccountSummary {
  account: AuthAccount;
  quota: { free: number; used: number; enforced: boolean };
  hasPaid: boolean;
  orders: number;
}

export const SESSION_KEY = 'auth.session';
export const TOKEN_KEY = 'auth.token';
/**
 * 匿名设备标识：`lib/api.ts` 在首次业务请求时生成并持久化这个 key。
 * 登录/注册时把它带给后端，后端会把该设备名下的匿名数据（简历/JD/报告/批次）认领到账号。
 */
export const DEVICE_ID_KEY = 'zhenresume.device_id';
export const SMS_RESEND_SECONDS = 60;

export const PATTERNS = {
  phone: /^1[3-9]\d{9}$/,
  email: /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/,
  /** 至少 8 位，且同时包含字母和数字 */
  password: /^(?=.*[A-Za-z])(?=.*\d).{8,}$/,
} as const;

/* ------------------------------------------------------------------ */
/* 小工具                                                              */
/* ------------------------------------------------------------------ */

export function maskPhone(phone: string): string {
  return phone.replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');
}

export function maskEmail(email: string): string {
  const [name, domain] = email.split('@');
  if (!name || !domain) return email;
  return `${name.length <= 1 ? name : `${name[0]}***`}@${domain}`;
}

/** 账号的展示名：优先用邮箱，没有邮箱（异常数据）就退回手机号 */
export function displayName(account: AuthAccount): string {
  if (account.email) return maskEmail(account.email);
  if (account.phone) return maskPhone(account.phone);
  return '未命名账号';
}

/* ------------------------------------------------------------------ */
/* 会话存取（localStorage）                                            */
/* ------------------------------------------------------------------ */

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeToken(token: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore */
  }
}

function readToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** 读匿名设备标识（只读不改写：没有就说明本机还没产生过匿名数据，无需认领）。 */
function readDeviceId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(DEVICE_ID_KEY);
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 无痕模式 / 配额满：忽略 */
  }
}

export function getSession(): AuthSession | null {
  const session = readJson<AuthSession | null>(SESSION_KEY, null);
  if (!session?.account) return null;
  return session;
}

export function saveSession(session: AuthSession): void {
  writeJson(SESSION_KEY, session);
}

export function clearSession(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(SESSION_KEY);
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ */
/* 网络层                                                              */
/* ------------------------------------------------------------------ */

const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';
const API_PREFIX = '/api/v1';

interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  account: {
    user_id: string;
    email: string | null;
    phone: string | null;
    role: string;
    auto_created: boolean;
    free_quota_used: number;
    created_at: string | null;
  };
  account_created?: boolean;
}

/**
 * 账号相关的请求单独走这里，而不是复用 lib/api.ts 的 request()：
 * 1. 登录/注册/发码这几个接口**不**该触发「401 → 跳登录页」的全局拦截；
 * 2. 登录本身就带着 token 需要写入，避免循环依赖。
 */
async function authFetch<T>(path: string, init: RequestInit & { token?: string | null } = {}): Promise<T> {
  const { token, ...rest } = init;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...((rest.headers as Record<string, string>) || {}),
  };
  if (rest.body !== undefined && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  // 登录/注册/验证码登录时带上匿名设备 ID：后端成功后会把这台设备名下的
  // 匿名简历/JD/报告/批次迁移到账号（见 backend/services/claim.py）。
  const deviceId = readDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;

  // 超时：后端不可达（或地址指向了别处）时，Fetch 在浏览器里可能挂很久，
  // 表现为「按钮一直转、什么都不发生」。15 秒兜底并给出可读错误。
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${API_PREFIX}${path}`, {
      ...rest,
      headers,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === 'AbortError';
    throw new Error(
      aborted
        ? `请求超时：后端没有响应（${API_BASE}）。请确认后端已启动、地址正确。`
        : `无法连接后端服务（${API_BASE}）。请确认后端已启动，且 CORS_ORIGINS 包含当前前端地址。`,
    );
  } finally {
    clearTimeout(timeoutId);
  }

  if (response.status === 204) return undefined as T;

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }

  if (!response.ok) {
    const detail = (parsed as { detail?: { code?: string; message?: string } } | null)?.detail;
    throw new Error(detail?.message || `请求失败（HTTP ${response.status}）`);
  }
  return parsed as T;
}

function toAccount(raw: AuthResponse['account']): AuthAccount {
  return {
    userId: raw.user_id,
    phone: raw.phone,
    email: raw.email,
    role: raw.role,
    autoCreated: Boolean(raw.auto_created),
    freeQuotaUsed: Number(raw.free_quota_used || 0),
    createdAt: raw.created_at,
  };
}

function persist(res: AuthResponse): AuthSession {
  const account = toAccount(res.account);
  const session: AuthSession = { account, display: displayName(account) };
  // ⚠️ token 必须以**原始字符串**存（不能 JSON.stringify）：`Authorization: Bearer <token>`
  // 需要裸 JWT，带引号或转义符会直接 401 AUTH_INVALID。lib/api.ts 的 getAuthToken 也按裸串读。
  writeToken(res.access_token);
  saveSession(session);
  return session;
}

/* ------------------------------------------------------------------ */
/* 对外 API                                                            */
/* ------------------------------------------------------------------ */

export const authApi = {
  getSession,

  /** 发送短信验证码；开发模式后端会返回 dev_code（生产模式为 null） */
  async sendSmsCode(phone: string, purpose: SmsPurpose): Promise<{ devCode: string | null; expiresIn: number }> {
    const res = await authFetch<{ dev_code: string | null; expires_in: number }>('/auth/sms-code', {
      method: 'POST',
      body: JSON.stringify({ phone, purpose }),
    });
    return { devCode: res.dev_code, expiresIn: res.expires_in };
  },

  /** 创建账号 */
  async register(payload: { email: string; phone: string; code: string; password: string }): Promise<AuthSession> {
    const res = await authFetch<AuthResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return persist(res);
  },

  /** 邮箱 + 密码登录 */
  async loginEmail(email: string, password: string): Promise<AuthSession> {
    const res = await authFetch<AuthResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    return persist(res);
  },

  /** 手机验证码登录；`accountCreated=true` 表示后端刚自动建号 */
  async loginSms(phone: string, code: string): Promise<{ session: AuthSession; accountCreated: boolean }> {
    const res = await authFetch<AuthResponse>('/auth/login-sms', {
      method: 'POST',
      body: JSON.stringify({ phone, code }),
    });
    return { session: persist(res), accountCreated: Boolean(res.account_created) };
  },

  /** 拉取当前账号 + 额度（进账号中心时用；401 由调用方跳登录） */
  async me(): Promise<AccountSummary> {
    const token = readToken();
    const res = await authFetch<{
      account: AuthResponse['account'];
      quota: { free: number; used: number; enforced: boolean };
      has_paid: boolean;
      orders: number;
    }>('/auth/me', { token });
    const account = toAccount(res.account);
    const session: AuthSession = { account, display: displayName(account) };
    saveSession(session);
    return { account, quota: res.quota, hasPaid: res.has_paid, orders: res.orders };
  },

  /** 改密码：旧 token 全部失效，后端会回一份新的 */
  async changePassword(payload: {
    oldPassword?: string;
    newPassword: string;
    confirmPassword: string;
  }): Promise<AuthSession> {
    const token = readToken();
    const res = await authFetch<AuthResponse>('/auth/change-password', {
      method: 'POST',
      token,
      body: JSON.stringify({
        old_password: payload.oldPassword ?? null,
        new_password: payload.newPassword,
        confirm_password: payload.confirmPassword,
      }),
    });
    return persist(res);
  },

  /** 改邮箱 */
  async updateEmail(email: string): Promise<AuthSession> {
    const token = readToken();
    const res = await authFetch<{ account: AuthResponse['account'] }>('/auth/me', {
      method: 'PATCH',
      token,
      body: JSON.stringify({ email }),
    });
    const account = toAccount(res.account);
    const session: AuthSession = { account, display: displayName(account) };
    saveSession(session);
    return session;
  },

  /** 登出：先尽力通知后端拉黑 jti，再清本地（后端不可达也照样退出） */
  async logout(): Promise<void> {
    const token = readToken();
    if (token) {
      try {
        await authFetch<void>('/auth/logout', { method: 'POST', token });
      } catch {
        /* 忽略：本地已经清掉，下次打开就是未登录 */
      }
    }
    clearSession();
  },
};
