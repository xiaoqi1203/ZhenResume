/**
 * ZhenResume（简甄）API 薄封装。
 *
 * 规则：
 * - base URL 来自 `NEXT_PUBLIC_API_BASE`，默认 `http://127.0.0.1:8000`；
 * - 所有业务接口前缀 `/api/v1`；
 * - 错误统一解析契约 §0 的 `{ detail: { code, message } }`，抛出携带 code/message 的 ApiError；
 * - 后端不可达时抛出 code 为 `NETWORK_ERROR` 的 ApiError。
 */

import type {
  AdminFeedbackListResponse,
  AdminMetrics,
  AdminPromptListResponse,
  BatchStatusResponse,
  CreateBatchPayload,
  CreateBatchResponse,
  CreateJdPayload,
  CreateKnowledgeDocPayload,
  CreateOrderPayload,
  CreateResumeFromEditorPayload,
  CreateTaskPayload,
  CreateTaskResponse,
  EditorResume,
  ExportFormat,
  HealthResponse,
  Jd,
  JdListResponse,
  KnowledgeDoc,
  KnowledgeDocListResponse,
  KnowledgeSearchPayload,
  KnowledgeSearchResponse,
  Order,
  PaginationParams,
  PaymentCallbackPayload,
  PaymentCallbackResponse,
  PlanListResponse,
  Report,
  Resume,
  ResumeListResponse,
  ShareResponse,
  SharedReport,
  SubmitFeedbackPayload,
  TaskListResponse,
  TaskStatusResponse,
  TemplateDetail,
  TemplateListResponse,
  UpdateResumePayload,
} from './types';

/* ------------------------------------------------------------------ */
/* 基础常量                                                            */
/* ------------------------------------------------------------------ */

export const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';

export const API_PREFIX = '/api/v1';

export const NETWORK_ERROR_MESSAGE =
  '无法连接后端服务，请确认后端已启动（默认 http://127.0.0.1:8000）';

/* ------------------------------------------------------------------ */
/* 错误类型                                                            */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  /** 业务错误码（契约 §8），或 `NETWORK_ERROR` / `HTTP_ERROR` */
  readonly code: string;
  /** HTTP 状态码；网络错误时为 0 */
  readonly status: number;

  constructor(code: string, message: string, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    // 兼容 ES5 目标下的 instanceof 判定
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

/** 契约 §8 错误码 → 中文兜底文案 */
const ERROR_CODE_MESSAGES: Record<string, string> = {
  VALIDATION_ERROR: '入参不合法，请检查填写内容后重试',
  UNSUPPORTED_FILE_TYPE: '简历文件类型不支持，请上传 PDF / DOCX / PNG / JPG / TXT',
  FILE_TOO_LARGE: '文件超过 10MB，请压缩后重试',
  RESUME_NOT_FOUND: '简历不存在',
  JD_NOT_FOUND: 'JD 不存在',
  TASK_NOT_FOUND: '任务不存在',
  REPORT_NOT_FOUND: '报告不存在',
  BATCH_NOT_FOUND: '批次不存在',
  PLAN_NOT_FOUND: '套餐不存在',
  TEMPLATE_NOT_FOUND: '简历模板不存在',
  PDF_UNAVAILABLE: '服务端未配置 PDF 转换组件，请改用 DOCX，或按 Ctrl+P 用浏览器打印为 PDF',
  QUOTA_EXCEEDED: '免费次数已用完',
  LLM_ERROR: 'LLM 调用失败且降级不可用，请稍后重试',
  INTERNAL_ERROR: '服务内部错误，请稍后重试',
  NETWORK_ERROR: NETWORK_ERROR_MESSAGE,
};

function fallbackMessageFor(code: string, status: number): string {
  if (ERROR_CODE_MESSAGES[code]) return ERROR_CODE_MESSAGES[code];
  if (status >= 500) return `服务异常（HTTP ${status}），请稍后重试`;
  if (status >= 400) return `请求失败（HTTP ${status}）`;
  return '请求失败，请稍后重试';
}

/* ------------------------------------------------------------------ */
/* URL / 请求工具                                                      */
/* ------------------------------------------------------------------ */

function withPagination(path: string, params?: PaginationParams): string {
  if (!params) return path;
  const search = new URLSearchParams();
  if (typeof params.limit === 'number') search.set('limit', String(params.limit));
  if (typeof params.offset === 'number') search.set('offset', String(params.offset));
  const qs = search.toString();
  return qs ? `${path}?${qs}` : path;
}

/** 拼接完整请求地址。 */
export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

/** 业务接口完整地址（自动补 `/api/v1` 前缀）。 */
export function apiV1Url(path: string): string {
  return apiUrl(`${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function extractErrorDetail(payload: unknown): { code: string; message: string } | null {
  if (!isRecord(payload)) return null;
  const detail = payload.detail;
  if (isRecord(detail)) {
    const code = typeof detail.code === 'string' ? detail.code : 'HTTP_ERROR';
    const message =
      typeof detail.message === 'string' && detail.message.length > 0
        ? detail.message
        : fallbackMessageFor(code, 0);
    return { code, message };
  }
  if (typeof detail === 'string' && detail.length > 0) {
    return { code: 'HTTP_ERROR', message: detail };
  }
  return null;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE' | 'PUT' | 'PATCH';
  /** JSON 请求体 */
  body?: unknown;
  /** multipart 请求体（与 body 互斥） */
  formData?: FormData;
  signal?: AbortSignal;
  /** 允许空响应体（如 204） */
  allowEmpty?: boolean;
}

/**
 * 设备标识：后端用 `X-Device-Id` 做匿名用户隔离（配额与历史记录都挂在这个用户上）。
 * MVP 未接登录，所以由浏览器生成并持久化一个 UUID；接真实认证时删掉这一段，
 * 改为在请求头带 Authorization 即可。
 */
const DEVICE_ID_STORAGE_KEY = 'zhenresume.device_id';

function getDeviceId(): string | null {
  // 服务端渲染阶段不发这个头，避免所有 SSR 请求被归到同一个 "server" 用户名下
  if (typeof window === 'undefined') return null;
  try {
    const existing = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY);
    if (existing) return existing;
    const generated =
      typeof window.crypto?.randomUUID === 'function'
        ? window.crypto.randomUUID()
        : `dev-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, generated);
    return generated;
  } catch {
    // 隐私模式下 localStorage 可能抛异常，退化为不带头（后端会归到 anonymous）
    return null;
  }
}

/**
 * 登录态：账号体系上线后，前端在 localStorage 里存一份 `auth.token`（见 lib/auth.ts）。
 * 这里只负责把它挂到请求头；**不**在这里 import lib/auth，避免循环依赖（auth 也要用 request）。
 */
const AUTH_TOKEN_STORAGE_KEY = 'auth.token';

function getAuthToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 401 统一处理：清掉本地登录态并跳登录页（带 next 回跳）。登录页本身不跳，避免死循环。 */
function handleUnauthorized(): void {
  if (typeof window === 'undefined') return;
  const { pathname, search } = window.location;
  try {
    window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem('auth.session');
    window.localStorage.removeItem('auth.accounts');
  } catch {
    /* ignore */
  }
  if (pathname.startsWith('/login')) return;
  const next = encodeURIComponent(pathname + search);
  window.location.assign(`/login?next=${next}`);
}
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, formData, signal, allowEmpty = false } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
  // 登录后优先走 Bearer（后端 get_current_user 双通道：Bearer 优先，回落 X-Device-Id）
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  let payloadBody: BodyInit | undefined;

  if (formData) {
    payloadBody = formData; // Content-Type 交给浏览器自动携带 boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payloadBody = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      body: payloadBody,
      signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new ApiError('NETWORK_ERROR', NETWORK_ERROR_MESSAGE, 0);
  }

  if (!response.ok) {
    const error = await toApiError(response);
    // 401 = 没登录 / token 失效：清本地登录态并去登录页（带回来路径）
    if (response.status === 401) handleUnauthorized();
    throw error;
  }

  if (allowEmpty || response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    if (response.status === 204) return undefined as T;
    throw new ApiError('INTERNAL_ERROR', '响应解析失败，返回内容不是合法 JSON', response.status);
  }
}

/**
 * 把非 2xx 响应解析成 ApiError（契约 §0 / §8）。
 * 供直接 `fetch` 文件流的场景（如简历导出，需要拿到状态码判断 `PDF_UNAVAILABLE`）复用。
 */
export async function toApiError(response: Response): Promise<ApiError> {
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  const detail = extractErrorDetail(parsed);
  if (detail) {
    const message = detail.message || fallbackMessageFor(detail.code, response.status);
    return new ApiError(detail.code, message, response.status);
  }
  return new ApiError(
    'HTTP_ERROR',
    fallbackMessageFor('HTTP_ERROR', response.status),
    response.status,
  );
}

/* ------------------------------------------------------------------ */
/* §1 健康检查                                                         */
/* ------------------------------------------------------------------ */

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>(`${API_PREFIX}/health`);
}

/* ------------------------------------------------------------------ */
/* §2 简历                                                             */
/* ------------------------------------------------------------------ */

/** 上传并解析简历（multipart/form-data）。 */
export function uploadResume(file: File, title?: string): Promise<Resume> {
  const formData = new FormData();
  formData.append('file', file);
  if (title) formData.append('title', title);
  return request<Resume>(`${API_PREFIX}/resumes`, { method: 'POST', formData });
}

export function listResumes(params?: PaginationParams): Promise<ResumeListResponse> {
  return request<ResumeListResponse>(withPagination(`${API_PREFIX}/resumes`, params));
}

export function getResume(resumeId: string): Promise<Resume> {
  return request<Resume>(`${API_PREFIX}/resumes/${resumeId}`);
}

export function deleteResume(resumeId: string): Promise<void> {
  return request<void>(`${API_PREFIX}/resumes/${resumeId}`, {
    method: 'DELETE',
    allowEmpty: true,
  });
}

/* ------------------------------------------------------------------ */
/* §3 JD                                                               */
/* ------------------------------------------------------------------ */

export function createJd(payload: CreateJdPayload): Promise<Jd> {
  return request<Jd>(`${API_PREFIX}/jds`, { method: 'POST', body: payload });
}

export function listJds(params?: PaginationParams): Promise<JdListResponse> {
  return request<JdListResponse>(withPagination(`${API_PREFIX}/jds`, params));
}

export function getJd(jdId: string): Promise<Jd> {
  return request<Jd>(`${API_PREFIX}/jds/${jdId}`);
}

export function deleteJd(jdId: string): Promise<void> {
  return request<void>(`${API_PREFIX}/jds/${jdId}`, { method: 'DELETE', allowEmpty: true });
}

/* ------------------------------------------------------------------ */
/* §4 分析任务                                                         */
/* ------------------------------------------------------------------ */

export function createTask(payload: CreateTaskPayload): Promise<CreateTaskResponse> {
  return request<CreateTaskResponse>(`${API_PREFIX}/tasks`, { method: 'POST', body: payload });
}

/** 轮询任务状态（契约建议 1.5s 一次）。 */
export function getTask(taskId: string, signal?: AbortSignal): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>(`${API_PREFIX}/tasks/${taskId}`, { signal });
}

export function listTasks(params?: PaginationParams): Promise<TaskListResponse> {
  return request<TaskListResponse>(withPagination(`${API_PREFIX}/tasks`, params));
}

/**
 * 任务进度流（契约 §4.4）。
 *
 * 为什么用 `fetch` + ReadableStream 而不是 `EventSource`：
 * 后端按 `X-Device-Id` 请求头鉴权，而 `EventSource` **不支持自定义请求头**。
 *
 * 为什么必须保留 `buf` 跨 chunk：一个 SSE 帧可能被 TCP 拆成两次 `read()`，
 * 逐 chunk 直接 `JSON.parse` 会偶发失败（手写 SSE 解析最常见的坑）。
 *
 * @returns 取消函数（组件卸载/重试时调用，内部走 AbortController）
 */
export function streamTask(
  taskId: string,
  handlers: {
    onEvent: (event: string, data: Record<string, unknown>) => void;
    onError: (error: unknown) => void;
    /** 流结束（服务端发完 done/error 后关闭，或对端关闭连接） */
    onClose: () => void;
  },
): () => void {
  const controller = new AbortController();
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  const authToken = getAuthToken();
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  void (async () => {
    try {
      const res = await fetch(apiUrl(`${API_PREFIX}/tasks/${taskId}/stream`), {
        headers,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`流式连接失败（HTTP ${res.status}）`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split('\n\n');
        buf = frames.pop() ?? '';
        for (const frame of frames) {
          const event = /^event: (.+)$/m.exec(frame)?.[1];
          const raw = /^data: (.+)$/m.exec(frame)?.[1];
          // 心跳是 `: ping` 注释行，没有 event/data，跳过
          if (!event || raw === undefined) continue;
          try {
            handlers.onEvent(event, JSON.parse(raw) as Record<string, unknown>);
          } catch {
            // 单帧解析失败不该拖垮整条流
          }
        }
      }
      handlers.onClose();
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      handlers.onError(err);
    }
  })();

  return () => controller.abort();
}

/**
 * HR 批次进度流（契约 §6.5）。
 *
 * 与 `streamTask` 的解析逻辑相同（同一个 SSE 帧格式、同样跨 chunk 保留 buffer），
 * 只是 URL 不同。**故意保持独立实现**：`streamTask` 已在 VM 上验证通过，
 * 抽公共函数要连带回归两条路径，等下一轮重构时再合并。
 */
export function streamBatch(
  batchId: string,
  handlers: {
    onEvent: (event: string, data: Record<string, unknown>) => void;
    onError: (error: unknown) => void;
    onClose: () => void;
  },
): () => void {
  const controller = new AbortController();
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  const authToken = getAuthToken();
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  void (async () => {
    try {
      const res = await fetch(apiUrl(`${API_PREFIX}/hr/batches/${batchId}/stream`), {
        headers,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`流式连接失败（HTTP ${res.status}）`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split('\n\n');
        buf = frames.pop() ?? '';
        for (const frame of frames) {
          const event = /^event: (.+)$/m.exec(frame)?.[1];
          const raw = /^data: (.+)$/m.exec(frame)?.[1];
          if (!event || raw === undefined) continue;
          try {
            handlers.onEvent(event, JSON.parse(raw) as Record<string, unknown>);
          } catch {
            // 单帧坏了不该拖垮整条流
          }
        }
      }
      handlers.onClose();
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      handlers.onError(err);
    }
  })();

  return () => controller.abort();
}

/* ------------------------------------------------------------------ */
/* §5 报告                                                             */
/* ------------------------------------------------------------------ */

export function getReport(reportId: string): Promise<Report> {
  return request<Report>(`${API_PREFIX}/reports/${reportId}`);
}

/** Markdown 导出地址（仅用于展示/调试；真正下载请用 downloadReportMarkdown）。 */
export function getReportMarkdownUrl(reportId: string): string {
  return apiV1Url(`/reports/${reportId}/markdown`);
}

/**
 * 下载报告 Markdown。
 *
 * ⚠️ 不能用 `<a href download>`：浏览器原生导航**不会携带 `X-Device-Id` 请求头**，
 * 后端会按 anonymous 用户处理 → 归属校验失败 → 404（这正是之前导出按钮点了没反应的根因）。
 * 所以这里走 fetch 拿 blob，再交给前端触发保存。
 */
export async function downloadReportMarkdown(
  reportId: string,
): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {};
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  const authToken = getAuthToken();
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  let response: Response;
  try {
    response = await fetch(apiV1Url(`/reports/${reportId}/markdown`), {
      headers,
      cache: 'no-store',
    });
  } catch {
    throw new ApiError('NETWORK_ERROR', NETWORK_ERROR_MESSAGE, 0);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    // 401 = 没登录 / token 失效：清本地登录态并去登录页（带回来路径）
    if (response.status === 401) handleUnauthorized();
    throw error;
  }

  const disposition = response.headers.get('content-disposition') ?? '';
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  let filename = `zhenresume-report-${reportId}.md`;
  if (utf8?.[1]) {
    try {
      filename = decodeURIComponent(utf8[1].trim());
    } catch {
      /* 解析失败用兜底文件名 */
    }
  } else if (plain?.[1]) {
    filename = plain[1].trim();
  }

  return { blob: await response.blob(), filename };
}

/** 生成分享链接（默认 7 天、不含简历原文；includeRaw=true 才带原文） */
export function createReportShare(reportId: string, includeRaw = false): Promise<ShareResponse> {
  return request<ShareResponse>(`${API_PREFIX}/reports/${reportId}/share`, {
    method: 'POST',
    body: { include_raw: includeRaw },
  });
}

/** 撤销该报告的全部分享链接 */
export function revokeReportShare(reportId: string): Promise<void> {
  return request<void>(`${API_PREFIX}/reports/${reportId}/share`, {
    method: 'DELETE',
    allowEmpty: true,
  });
}

/** 公开读取分享内容（无需设备 ID） */
export function getSharedReport(token: string): Promise<SharedReport> {
  return request<SharedReport>(`${API_PREFIX}/shares/${token}`);
}

/* ------------------------------------------------------------------ */
/* §6.1 定价与支付                                                     */
/* ------------------------------------------------------------------ */

export function listPlans(): Promise<PlanListResponse> {
  return request<PlanListResponse>(`${API_PREFIX}/plans`);
}

export function createOrder(payload: CreateOrderPayload): Promise<Order> {
  return request<Order>(`${API_PREFIX}/payments/orders`, { method: 'POST', body: payload });
}

export function paymentCallback(
  payload: PaymentCallbackPayload,
): Promise<PaymentCallbackResponse> {
  return request<PaymentCallbackResponse>(`${API_PREFIX}/payments/callback`, {
    method: 'POST',
    body: payload,
  });
}

/* ------------------------------------------------------------------ */
/* §6.2 知识库 RAG                                                     */
/* ------------------------------------------------------------------ */

export function createKnowledgeDoc(payload: CreateKnowledgeDocPayload): Promise<KnowledgeDoc> {
  return request<KnowledgeDoc>(`${API_PREFIX}/knowledge/docs`, {
    method: 'POST',
    body: payload,
  });
}

export function listKnowledgeDocs(
  params?: PaginationParams & { scope?: string },
): Promise<KnowledgeDocListResponse> {
  const base = withPagination(`${API_PREFIX}/knowledge/docs`, params);
  if (!params?.scope) return request<KnowledgeDocListResponse>(base);
  const search = new URLSearchParams({ scope: params.scope });
  if (typeof params.limit === 'number') search.set('limit', String(params.limit));
  if (typeof params.offset === 'number') search.set('offset', String(params.offset));
  return request<KnowledgeDocListResponse>(
    `${API_PREFIX}/knowledge/docs?${search.toString()}`,
  );
}

export function searchKnowledge(
  payload: KnowledgeSearchPayload,
): Promise<KnowledgeSearchResponse> {
  return request<KnowledgeSearchResponse>(`${API_PREFIX}/knowledge/search`, {
    method: 'POST',
    body: payload,
  });
}

/* ------------------------------------------------------------------ */
/* §6.3 管理后台                                                       */
/* ------------------------------------------------------------------ */

export function getAdminMetrics(): Promise<AdminMetrics> {
  return request<AdminMetrics>(`${API_PREFIX}/admin/metrics`);
}

export function listAdminPrompts(): Promise<AdminPromptListResponse> {
  return request<AdminPromptListResponse>(`${API_PREFIX}/admin/prompts`);
}

export function createAdminPrompt(name: string, content: string): Promise<unknown> {
  return request<unknown>(`${API_PREFIX}/admin/prompts`, {
    method: 'POST',
    body: { name, content },
  });
}

export function listAdminFeedback(
  params?: PaginationParams,
): Promise<AdminFeedbackListResponse> {
  return request<AdminFeedbackListResponse>(
    withPagination(`${API_PREFIX}/admin/feedback`, params),
  );
}

/* ------------------------------------------------------------------ */
/* §6.4 用户反馈                                                       */
/* ------------------------------------------------------------------ */

export function submitFeedback(payload: SubmitFeedbackPayload): Promise<unknown> {
  return request<unknown>(`${API_PREFIX}/feedback`, { method: 'POST', body: payload });
}

/* ------------------------------------------------------------------ */
/* §6.5 HR 批量初筛                                                    */
/* ------------------------------------------------------------------ */

export function createBatch(payload: CreateBatchPayload): Promise<CreateBatchResponse> {
  return request<CreateBatchResponse>(`${API_PREFIX}/hr/batches`, {
    method: 'POST',
    body: payload,
  });
}

export function getBatch(batchId: string, signal?: AbortSignal): Promise<BatchStatusResponse> {
  return request<BatchStatusResponse>(`${API_PREFIX}/hr/batches/${batchId}`, { signal });
}

/** CSV 导出地址（仅用于展示/调试）。真实下载请用 `downloadBatchExport()`，原因同简历导出。 */
export function getBatchExportCsvUrl(batchId: string): string {
  return apiV1Url(`/hr/batches/${batchId}/export.csv`);
}

/**
 * 下载 HR 批次 CSV。
 *
 * 与 `downloadResumeExport` 同理：批次导出接口按 `X-Device-Id` 校验归属，
 * `<a href download>` 的原生导航不带这个头，会直接 404 BATCH_NOT_FOUND。
 */
export async function downloadBatchExport(batchId: string): Promise<DownloadedFile> {
  const headers: Record<string, string> = {};
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  const authToken = getAuthToken();
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  let response: Response;
  try {
    response = await fetch(getBatchExportCsvUrl(batchId), { headers, cache: 'no-store' });
  } catch {
    throw new ApiError('NETWORK_ERROR', NETWORK_ERROR_MESSAGE, 0);
  }

  if (!response.ok) {
    const error = await toApiError(response);
    // 401 = 没登录 / token 失效：清本地登录态并去登录页（带回来路径）
    if (response.status === 401) handleUnauthorized();
    throw error;
  }

  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get('Content-Disposition'),
    // CSV 导出没有分页概念
    pageCount: null,
    fitScale: null,
  };
}

/* ------------------------------------------------------------------ */
/* §6.6 简历模板                                                       */
/* ------------------------------------------------------------------ */

export function listTemplates(): Promise<TemplateListResponse> {
  return request<TemplateListResponse>(`${API_PREFIX}/templates`);
}

export function getTemplate(code: string): Promise<TemplateDetail> {
  return request<TemplateDetail>(`${API_PREFIX}/templates/${encodeURIComponent(code)}`);
}

/* ------------------------------------------------------------------ */
/* §2.3 编辑器创建简历 / §2.4 导出                                      */
/* ------------------------------------------------------------------ */

/** 用结构化内容创建简历（不走文件上传，返回带 `editor_content` 的简历）。 */
export function createResumeFromEditor(
  payload: CreateResumeFromEditorPayload,
): Promise<EditorResume> {
  return request<EditorResume>(`${API_PREFIX}/resumes/from-editor`, {
    method: 'POST',
    body: payload,
  });
}

/**
 * 更新已有简历（`PUT /api/v1/resumes/{resume_id}`）。
 *
 * 编辑器「保存到账号」/ 导出 / 发起诊断都走这里，避免每改一次就新建一份。
 * 后端会按 `X-Device-Id`（或 Bearer）校验归属，非本人返回 404 RESUME_NOT_FOUND。
 */
export function updateResume(
  resumeId: string,
  payload: UpdateResumePayload,
): Promise<EditorResume> {
  return request<EditorResume>(`${API_PREFIX}/resumes/${resumeId}`, {
    method: 'PUT',
    body: payload,
  });
}

/**
 * 简历导出地址（仅用于展示/调试）。
 *
 * ⚠️ 不要把它直接塞进 `<a href download>`：浏览器的原生导航**不会带上 `X-Device-Id`
 * 自定义请求头**，而后端按设备校验简历归属，结果是 404 RESUME_NOT_FOUND。
 * 真实下载请用 `downloadResumeExport()`。
 */
export function getResumeExportUrl(resumeId: string, format: ExportFormat): string {
  return apiV1Url(`/resumes/${resumeId}/export/${format}`);
}

export interface DownloadedFile {
  blob: Blob;
  /** 原始 Content-Disposition，交给调用方解析文件名 */
  contentDisposition: string | null;
  /** PDF 实际页数（后端 X-Page-Count 头） */
  pageCount: number | null;
  /** fit=one 时实际使用的缩放比例（后端 X-Fit-Scale 头） */
  fitScale: number | null;
}

/**
 * 下载简历导出文件。
 *
 * 用 fetch 而不是 `<a download>` 的唯一原因是请求头：后端按 `X-Device-Id` 校验归属，
 * 只有 fetch 能带上它。顺带的好处是能读到状态码，从而区分 503 PDF_UNAVAILABLE
 * （服务端没配 LibreOffice），给出「改用 DOCX 或浏览器打印」的提示。
 *
 * `fit="one"` 只对 PDF 生效：后端会逐档缩小字号把内容压到 1 页，
 * 实际页数与缩放比例在响应头里返回。
 */
export async function downloadResumeExport(
  resumeId: string,
  format: ExportFormat,
  options: { fit?: 'none' | 'one' } = {},
): Promise<DownloadedFile> {
  const headers: Record<string, string> = {};
  const deviceId = getDeviceId();
  if (deviceId) headers['X-Device-Id'] = deviceId;
  const authToken = getAuthToken();
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const query = options.fit === 'one' ? '?fit=one' : '';
  const url = `${getResumeExportUrl(resumeId, format)}${query}`;

  let response: Response;
  try {
    response = await fetch(url, { headers, cache: 'no-store' });
  } catch {
    throw new ApiError('NETWORK_ERROR', NETWORK_ERROR_MESSAGE, 0);
  }

  if (!response.ok) {
    const error = await toApiError(response);
    // 401 = 没登录 / token 失效：清本地登录态并去登录页（带回来路径）
    if (response.status === 401) handleUnauthorized();
    throw error;
  }

  const pageCount = Number(response.headers.get('X-Page-Count') ?? '');
  const fitScale = Number(response.headers.get('X-Fit-Scale') ?? '');

  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get('Content-Disposition'),
    pageCount: Number.isFinite(pageCount) && pageCount > 0 ? pageCount : null,
    fitScale: Number.isFinite(fitScale) && fitScale > 0 ? fitScale : null,
  };
}

/* ------------------------------------------------------------------ */
/* 分组导出（便于 `import { api } from '@/lib/api'`）                  */
/* ------------------------------------------------------------------ */

export const api = {
  // 健康检查
  getHealth,
  // 简历
  uploadResume,
  listResumes,
  getResume,
  deleteResume,
  // JD
  createJd,
  listJds,
  getJd,
  deleteJd,
  // 任务
  createTask,
  getTask,
  listTasks,
  // 报告
  getReport,
  getReportMarkdownUrl,
  downloadReportMarkdown,
  createReportShare,
  revokeReportShare,
  getSharedReport,
  // 定价与支付
  listPlans,
  createOrder,
  paymentCallback,
  // 知识库
  createKnowledgeDoc,
  listKnowledgeDocs,
  searchKnowledge,
  // 管理后台
  getAdminMetrics,
  listAdminPrompts,
  createAdminPrompt,
  listAdminFeedback,
  // 反馈
  submitFeedback,
  // HR 批量
  createBatch,
  getBatch,
  getBatchExportCsvUrl,
  downloadBatchExport,
  // 简历模板
  listTemplates,
  getTemplate,
  // 编辑器
  createResumeFromEditor,
  updateResume,
  getResumeExportUrl,
  downloadResumeExport,
  // 工具
  apiUrl,
  apiV1Url,
  toApiError,
  ApiError,
  API_BASE,
};

export default api;
