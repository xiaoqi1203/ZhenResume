/**
 * ZhenResume（简甄）前端类型定义。
 * 严格对齐 docs/api-contract.md v1（冻结版），字段名与枚举值不得改动。
 */

/* ------------------------------------------------------------------ */
/* §0 通用                                                             */
/* ------------------------------------------------------------------ */

/** §0 通用错误响应体：{ "detail": { "code": "...", "message": "..." } } */
export interface ApiErrorDetail {
  code: string;
  message: string;
}

export interface ApiErrorBody {
  detail: ApiErrorDetail;
}

/** §0 分页列表响应 */
export interface Paginated<T> {
  items: T[];
  total: number;
}

export interface PaginationParams {
  limit?: number;
  offset?: number;
}

/* ------------------------------------------------------------------ */
/* §1 健康检查                                                         */
/* ------------------------------------------------------------------ */

export type LlmMode = 'live' | 'mock';

export interface HealthResponse {
  status: string;
  version: string;
  llm_mode: LlmMode;
  llm_provider: string;
  llm_model: string;
}

/* ------------------------------------------------------------------ */
/* §7.1 ParsedResume                                                   */
/* ------------------------------------------------------------------ */

export interface ResumeBasics {
  name: string;
  email: string;
  phone: string;
  city: string;
  links: string[];
}

export interface ResumeEducation {
  school: string;
  degree: string;
  major: string;
  start: string;
  end: string;
  highlights: string[];
}

export interface ResumeWork {
  company: string;
  title: string;
  start: string;
  end: string;
  location: string;
  highlights: string[];
}

export interface ResumeProject {
  name: string;
  role: string;
  start: string;
  end: string;
  highlights: string[];
  tech: string[];
}

export type TimelineItemType = 'education' | 'work' | 'project';

export interface ResumeTimelineItem {
  start: string;
  end: string;
  label: string;
  type: TimelineItemType;
}

/** 所有键始终存在；无内容时为空数组或空字符串。 */
export interface ParsedResume {
  basics: ResumeBasics;
  education: ResumeEducation[];
  work: ResumeWork[];
  projects: ResumeProject[];
  skills: string[];
  certificates: string[];
  timeline: ResumeTimelineItem[];
}

/* ------------------------------------------------------------------ */
/* §7.2 ParsedJD                                                       */
/* ------------------------------------------------------------------ */

export interface ParsedJD {
  title: string;
  company: string;
  seniority: string;
  experience_years: number | null;
  education: string;
  skills_core: string[];
  skills_bonus: string[];
  responsibilities: string[];
  keywords: string[];
}

/* ------------------------------------------------------------------ */
/* §2 简历                                                             */
/* ------------------------------------------------------------------ */

/**
 * §2.1 `source` ∈ `upload` | `editor`。
 * 决定报告页「修改后重新诊断」跳回上传首页还是简历编辑器。
 */
export type ResumeSource = 'upload' | 'editor';

/** §2.1 上传/详情响应 */
export interface Resume {
  resume_id: string;
  title: string;
  file_url: string;
  raw_text: string;
  parsed: ParsedResume;
  source: ResumeSource;
  /** 仅编辑器创建的简历有值；上传的简历为 `null` */
  editor_content: EditorContent | null;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/* §2.5 EditorContent（结构化编辑器内容）                              */
/* ------------------------------------------------------------------ */

/** 所有键始终存在；无内容时为空字符串或空数组。 */
export interface EditorBasics {
  name: string;
  target_title: string;
  phone: string;
  email: string;
  city: string;
  links: string[];
}

export interface EditorWorkItem {
  company: string;
  title: string;
  start: string;
  end: string;
  location: string;
  highlights: string[];
}

export interface EditorProjectItem {
  name: string;
  role: string;
  start: string;
  end: string;
  tech: string[];
  highlights: string[];
}

export interface EditorEducationItem {
  school: string;
  degree: string;
  major: string;
  start: string;
  end: string;
}

export interface EditorCustomSection {
  title: string;
  items: string[];
}

/**
 * §2.5 结构化简历内容。
 * 后端会把它渲染成 `raw_text`，诊断流水线与上传文件完全一致。
 */
export interface EditorContent {
  basics: EditorBasics;
  summary: string;
  work: EditorWorkItem[];
  projects: EditorProjectItem[];
  education: EditorEducationItem[];
  skills: string[];
  certificates: string[];
  awards: string[];
  custom_sections: EditorCustomSection[];
}

/** §2.3 编辑器创建简历的响应（`source` 恒为 `editor`）。 */
export type EditorResume = Resume & {
  source: 'editor';
  editor_content: EditorContent;
};

/** §2.4 导出格式。 */
export type ExportFormat = 'markdown' | 'docx' | 'pdf';

/** 空内容工厂：所有键都在、数组为空、字符串为空。 */
export function createEmptyEditorContent(): EditorContent {
  return {
    basics: {
      name: '',
      target_title: '',
      phone: '',
      email: '',
      city: '',
      links: [],
    },
    summary: '',
    work: [],
    projects: [],
    education: [],
    skills: [],
    certificates: [],
    awards: [],
    custom_sections: [],
  };
}

/** §2.2 列表项 */
export interface ResumeSummary {
  resume_id: string;
  title: string;
  created_at: string;
  parsed_name: string;
  parsed_title: string;
  /** 来源：`editor` 的简历带结构化内容，可在编辑器里无损往返 */
  source: ResumeSource;
}

export type ResumeListResponse = Paginated<ResumeSummary>;

/* ------------------------------------------------------------------ */
/* §3 JD                                                               */
/* ------------------------------------------------------------------ */

export interface Jd {
  jd_id: string;
  title: string;
  company: string;
  raw_text: string;
  parsed: ParsedJD;
  created_at: string;
}

export interface JdSummary {
  jd_id: string;
  title: string;
  company: string;
  created_at: string;
}

export type JdListResponse = Paginated<JdSummary>;

export interface CreateJdPayload {
  title: string;
  company?: string;
  raw_text: string;
}

/* ------------------------------------------------------------------ */
/* §4 分析任务                                                         */
/* ------------------------------------------------------------------ */

export type TaskStatus = 'pending' | 'running' | 'done' | 'failed';

/** 8 个 Agent 步骤，顺序固定。 */
export type StepName =
  | 'parse'
  | 'jd_match'
  | 'ats'
  | 'impact'
  | 'risk'
  | 'suggestion'
  | 'report'
  | 'qc';

export type StepStatus = 'pending' | 'running' | 'done' | 'failed';

export interface TaskStep {
  step_name: StepName;
  status: StepStatus;
  latency_ms: number | null;
  tokens: number | null;
  cost: number | null;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  /** [0, 1] */
  progress: number;
  current_step: StepName | null;
  total_steps: number;
  steps: TaskStep[];
  report_id: string | null;
  error: string | null;
}

export interface CreateTaskPayload {
  resume_id: string;
  jd_id: string;
}

export interface CreateTaskResponse {
  task_id: string;
  status: TaskStatus;
}

export interface TaskSummary {
  task_id: string;
  resume_id: string;
  jd_id: string;
  status: TaskStatus;
  created_at: string;
  report_id: string | null;
}

export type TaskListResponse = Paginated<TaskSummary>;

/* ------------------------------------------------------------------ */
/* §5 报告                                                             */
/* ------------------------------------------------------------------ */

export type RiskLevel = 'red' | 'yellow' | 'green';

/** risk 分越高表示风险越低。 */
export interface ScoreBreakdown {
  overall: number;
  ats: number;
  impact: number;
  clarity: number;
  match: number;
  risk: number;
}

export type FindingCategory = 'ats' | 'impact' | 'clarity' | 'risk' | 'gap';

/** severity 复用了红/黄/绿三级。 */
export type FindingSeverity = RiskLevel;

export type EvidenceSource = 'resume' | 'jd' | 'knowledge';

export interface FindingEvidence {
  source: EvidenceSource;
  quote: string;
  /** [start, end] 字符偏移 */
  span: [number, number];
  field: string;
}

export interface Finding {
  finding_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  title: string;
  original_text: string;
  issue: string;
  evidence: FindingEvidence;
  suggestion: string;
  rewritten_text: string;
}

export type KnowledgeScope = 'role' | 'industry' | 'resume_template' | 'compliance';

export interface KnowledgeRef {
  doc_id: string;
  chunk_id: string;
  title: string;
  scope: KnowledgeScope;
}

export interface Report {
  report_id: string;
  task_id: string;
  resume_id: string;
  resume_title: string;
  resume_source: ResumeSource;
  /** 简历是否带结构化内容（编辑器创建的简历为 true） */
  has_editor_content: boolean;
  jd_title: string;
  created_at: string;
  scores: ScoreBreakdown;
  risk_level: RiskLevel;
  summary: string;
  /** 至少 5 条，已按 severity 排序：red → yellow → green */
  findings: Finding[];
  missing_keywords: string[];
  action_items: string[];
  knowledge_refs: KnowledgeRef[];
  disclaimer: string;
}

/** §5.2 分享链接 */
export interface ShareResponse {
  share_url: string;
  expires_at: string | null;
}

/* ------------------------------------------------------------------ */
/* §6.1 定价与支付                                                     */
/* ------------------------------------------------------------------ */

/** 套餐 code 固定集合。 */
export type PlanCode =
  | 'free'
  | 'single_basic'
  | 'single_deep'
  | 'monthly_basic'
  | 'monthly_pro'
  | 'hr_basic'
  | 'hr_pro';

export type PlanPeriod = 'once' | 'month';

export interface Plan {
  code: PlanCode | string;
  name: string;
  price: number;
  currency: string;
  period: PlanPeriod;
  quota: number;
  features: string[];
}

export interface PlanListResponse {
  items: Plan[];
}

export type PaymentProvider = 'stripe' | 'wechat' | 'alipay';

export interface CreateOrderPayload {
  plan_code: string;
  provider?: PaymentProvider;
}

export type OrderStatus = 'pending' | 'paid' | 'failed' | 'canceled';

export interface Order {
  order_no: string;
  plan_code: string;
  amount: number;
  currency: string;
  provider: PaymentProvider;
  status: OrderStatus;
  pay_url: string;
}

export interface PaymentCallbackPayload {
  order_no: string;
  status: string;
}

export interface PaymentCallbackResponse {
  ok: boolean;
  order_no: string;
  status: string;
}

/* ------------------------------------------------------------------ */
/* §6.2 知识库 RAG                                                     */
/* ------------------------------------------------------------------ */

export type KnowledgeDocType =
  | 'skill_model'
  | 'industry_report'
  | 'resume_template'
  | 'compliance'
  | string;

export interface KnowledgeDoc {
  doc_id: string;
  scope: KnowledgeScope;
  role_family: string;
  doc_type: KnowledgeDocType;
  title: string;
  content: string;
  created_at?: string;
}

export interface CreateKnowledgeDocPayload {
  scope: KnowledgeScope;
  role_family: string;
  doc_type: KnowledgeDocType;
  title: string;
  content: string;
}

export interface KnowledgeHit {
  chunk_id: string;
  doc_id: string;
  title: string;
  scope: KnowledgeScope;
  score: number;
  text: string;
}

export interface KnowledgeSearchPayload {
  query: string;
  top_k?: number;
  scope?: KnowledgeScope;
}

export interface KnowledgeSearchResponse {
  items: KnowledgeHit[];
}

export type KnowledgeDocListResponse = Paginated<KnowledgeDoc>;

/* ------------------------------------------------------------------ */
/* §6.3 管理后台                                                       */
/* ------------------------------------------------------------------ */

export interface StepBreakdownItem {
  step_name: StepName | string;
  calls: number;
  avg_latency_ms: number;
  cost: number;
}

export interface AdminMetrics {
  tasks_total: number;
  tasks_done: number;
  tasks_failed: number;
  reports_total: number;
  avg_latency_ms: number;
  tokens_total: number;
  cost_total: number;
  cost_per_report: number;
  feedback_avg_rating: number;
  /** 目标 100%（aaa.md §7 成功指标） */
  fairness_pass_rate: number;
  step_breakdown: StepBreakdownItem[];
}

export interface AdminPrompt {
  name: string;
  version: number;
  updated_at: string;
}

export type AdminPromptListResponse = Paginated<AdminPrompt>;

export interface AdminFeedback {
  feedback_id?: string;
  report_id: string;
  rating: number;
  comment: string;
  created_at?: string;
}

export type AdminFeedbackListResponse = Paginated<AdminFeedback>;

/* ------------------------------------------------------------------ */
/* §6.4 用户反馈                                                       */
/* ------------------------------------------------------------------ */

export interface SubmitFeedbackPayload {
  report_id: string;
  rating: number;
  comment?: string;
}

export interface Feedback {
  report_id: string;
  rating: number;
  comment: string;
}

/* ------------------------------------------------------------------ */
/* §6.5 HR 批量初筛                                                    */
/* ------------------------------------------------------------------ */

export type BatchStatus = 'pending' | 'running' | 'done' | 'failed';

export type BatchItemStatus = 'pending' | 'running' | 'done' | 'failed';

export interface BatchItem {
  resume_id: string;
  resume_title: string;
  status: BatchItemStatus;
  overall_score: number | null;
  match_score: number | null;
  risk_level: RiskLevel | null;
  report_id: string | null;
  summary: string;
}

export interface CreateBatchPayload {
  jd_id: string;
  resume_ids: string[];
}

export interface CreateBatchResponse {
  batch_id: string;
  status: BatchStatus;
  total: number;
}

export interface BatchStatusResponse {
  batch_id: string;
  status: BatchStatus;
  total: number;
  finished: number;
  /** 列表按 overall_score 降序 */
  items: BatchItem[];
}

/* ------------------------------------------------------------------ */
/* §6.6 简历模板                                                       */
/* ------------------------------------------------------------------ */

/** 模板适用人群：应届 / 社招 / 转行。 */
export type TemplateTarget = 'campus' | 'experienced' | 'transition';

/** `sections[].key` 的固定集合。 */
export type TemplateSectionKey =
  | 'basics'
  | 'summary'
  | 'work'
  | 'projects'
  | 'education'
  | 'skills'
  | 'certificates'
  | 'awards'
  | 'custom';

/** §6.6 列表项 */
export interface TemplateSummary {
  code: string;
  name: string;
  description: string;
  target: TemplateTarget;
  role_family: string;
  section_count: number;
}

export interface TemplateSection {
  key: TemplateSectionKey;
  title: string;
  required: boolean;
  tip: string;
  examples: string[];
  /** `null` 表示该区块条目数不限 */
  max_items: number | null;
}

/** §6.6 详情（含示例内容与写作规则）。 */
export interface TemplateDetail extends TemplateSummary {
  intro: string;
  writing_rules: string[];
  sections: TemplateSection[];
  /** 一份填好的 EditorContent，供一键载入 */
  example_content: EditorContent;
}

export interface TemplateListResponse {
  items: TemplateSummary[];
}

export interface CreateResumeFromEditorPayload {
  title: string;
  template_code: string | null;
  content: EditorContent;
}
