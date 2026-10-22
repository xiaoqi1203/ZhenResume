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
  /** 目标岗位（上传解析常拿不到，缺省视为空串；编辑器导入时回填） */
  target_title?: string;
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
  /**
   * 编辑器保存时选择的模板 code；未选模板或上传的简历为 `null`。
   * 用于「继续编辑」时把模板详情回填到编辑器。
   */
  template_code?: string | null;
  created_at: string;
  /**
   * 抽取方法，例如 `pypdf` / `python-docx` / `ocr/rapidocr` / `ocr-unavailable`。
   * 仅上传接口会返回（去重命中时为 `dedup`），详情/列表接口没有这个字段。
   */
  extract_method?: string | null;
  /** 降级原因：OCR 引擎缺失、扫描件无文本层等。前端据此提示用户，而不是静默继续。 */
  extract_note?: string | null;
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
  /** 头像 data URL（前端压缩到 480px 内），仅用于导出 PDF/DOCX，不参与诊断 */
  avatar?: string;
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
      avatar: '',
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
  /** 编辑器创建时选的模板 code（上传的简历为 null） */
  template_code?: string | null;
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

/**
 * 一次诊断的真实消耗（"让用户看到耗时和花费"）。
 *
 * 每个字段都有存在理由，不要精简成单个字段：
 * - `cost` 是**总花费**，含证据 Agent；`steps_cost` / `evidence_cost` 拆开是为了
 *   让用户（和排查问题的我们）知道钱花在哪 —— 证据 Agent 经常比 8 个主步骤加起来还贵；
 * - `latency_ms` 是端到端墙钟（并行步骤会重叠，不能把 steps 相加）；
 * - 未完成时这些是"已发生"的部分值，所以前端可以边跑边显示。
 */
export interface TaskUsage {
  latency_ms: number;
  tokens: number;
  cost: number;
  steps_cost: number;
  evidence_cost: number;
  evidence_tokens: number;
  evidence_latency_ms: number;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  /** fixed = 固定流水线（快）；agentic = Agent 自主取证（慢但引用更全） */
  mode?: 'fixed' | 'agentic';
  /** [0, 1] */
  progress: number;
  current_step: StepName | null;
  total_steps: number;
  steps: TaskStep[];
  report_id: string | null;
  error: string | null;
  /** ISO 时间戳（旧后端可能没有 → 前端按 null 处理） */
  created_at?: string | null;
  completed_at?: string | null;
  usage?: TaskUsage | null;
}

export interface CreateTaskPayload {
  resume_id: string;
  jd_id: string;
  /** 分析模式，默认 fixed */
  mode?: 'fixed' | 'agentic';
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
  /**
   * [start, end] 字符偏移。**可能缺失或为 null**：
   * 规则层的结构性结论（覆盖率、篇幅、时间线计算）本来就不针对某一句原文，
   * 引文定位失败时后端也会给 null。前端必须按可选处理，不要默认成 [0, 0]。
   */
  span?: [number, number] | null;
  /** 证据落在哪个字段（如 `projects[0].highlights`）；规则类结论可能为空 */
  field?: string | null;
  /** 后端明确标记"这句引文没能在原文里精确定位" */
  unverified?: boolean;
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
  /** 该条结论的知识库依据（agentic 模式下由 Agent 检索并经服务端校验） */
  citations?: FindingCitation[];
}

export type KnowledgeScope = 'role' | 'industry' | 'resume_template' | 'compliance';

export interface KnowledgeRef {
  doc_id: string;
  chunk_id: string;
  title: string;
  scope: KnowledgeScope;
}

/** 知识库 chunk 级引用（Agentic RAG 产出，服务端已逐字校验） */
export interface FindingCitation {
  chunk_id: string;
  doc_id?: string | null;
  doc_title: string;
  quote: string;
  verified: boolean;
}

/** Agent 一次运行的轨迹（报告 extra_json.agent_trace 透出） */
export interface AgentTrace {
  engine: string;
  agent?: string;
  status: string;
  notes?: string | null;
  model_calls?: number;
  tool_calls?: number;
  retrieval_requests?: number;
  retrieval_rounds?: number;
  retrieval_blocked?: number;
  retrieval_queries?: string[];
  self_checks?: number;
  reasoning?: string;
  verify?: {
    total: number;
    verified: number;
    rejected_missing_chunk?: number;
    rejected_quote_not_found?: number;
    rejected_duplicate?: number;
  };
  attach?: { attached: number; unmatched: number };
  tokens?: number;
  cost?: number;
  latency_ms?: number;
  truncated?: boolean;
  forced_finish?: boolean;
  error?: string;
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
  /**
   * 双分制的"深度分"（仅 agentic 模式；fixed 模式为 null）。
   * 与 `scores` 用同一套公式，唯一差别是**模型补充的发现项也参与扣分**。
   * `delta` = 规则分 - 深度分（>=0），`extra_findings` = 模型独有的发现项数。
   */
  depth_scores?: DepthScores | null;
  summary: string;
  /** 至少 5 条，已按 severity 排序：red → yellow → green */
  findings: Finding[];
  missing_keywords: string[];
  action_items: string[];
  knowledge_refs: KnowledgeRef[];
  /** Agentic RAG 轨迹；fixed 模式为 null */
  agent_trace?: AgentTrace | null;
  disclaimer: string;
}

/** 双分制的深度分（见 Report.depth_scores） */
export interface DepthScores {
  overall: number;
  ats: number;
  impact: number;
  clarity: number;
  match: number;
  risk: number;
  /** 规则分 - 深度分（>=0；0 表示模型没有发现规则之外的新问题） */
  delta: number;
  /** 模型独有的发现项数量 */
  extra_findings: number;
  /** 按维度拆分的模型独有发现项数量 */
  extra_by_category: Record<string, number>;
  /** 口径说明文案（后端生成，避免前端各写一版解释） */
  explain: string;
}

/** §5.2 分享链接 */
export interface ShareResponse {
  share_url: string;
  expires_at: string | null;
  /** 是否包含简历原文引用 */
  include_raw: boolean;
  ttl_days: number;
}

/** 分享页公开数据：结论卡（默认不含原文；include_raw 时才有） */
export interface SharedFinding {
  finding_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  title: string;
  issue: string;
  suggestion: string;
  original_text?: string;
  rewritten_text?: string;
  evidence?: FindingEvidence;
}

export interface SharedReport {
  report_id: string;
  created_at: string;
  /** 简历原始文件名（用户选择展示） */
  resume_title: string;
  jd_title: string;
  scores: ScoreBreakdown;
  risk_level: RiskLevel;
  summary: string;
  findings: SharedFinding[];
  missing_keywords: string[];
  action_items: string[];
  disclaimer: string;
  include_raw: boolean;
  expires_at: string | null;
  view_count: number;
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

/**
 * 模板气质（画廊「风格」筛选的依据）。
 * 与后端 `services/resume_templates.py` 的 `STYLES` 一一对应。
 */
export type TemplateStyle = 'minimal' | 'business' | 'fresh' | 'creative';

/**
 * 版面骨架（画廊「布局」筛选的依据）：
 * `single` = 单栏（ATS 友好），`sidebar` = 双栏 / 侧边栏。
 */
export type TemplateLayout = 'single' | 'sidebar';

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

/**
 * §6.6 列表项。
 *
 * 版式元数据（`style` / `layout` / `design` / `accent` / `tags`）是后加的字段：
 * 老后端（或缓存数据）可能没有，因此全部可选 —— 画廊会按 `design` / `target`
 * 做兜底推断，缺字段不会让筛选整个失效。
 */
export interface TemplateSummary {
  code: string;
  name: string;
  description: string;
  target: TemplateTarget;
  role_family: string;
  section_count: number;
  /** 整体气质，决定画廊「风格」tab */
  style?: TemplateStyle;
  /** 版面骨架，决定画廊「布局」筛选 */
  layout?: TemplateLayout;
  /** 真实版式的设计 id（celadon / navy / ivory / cobalt），对应 `TemplateDesignId` */
  design?: string;
  /** 主强调色（例如 `#5D7F6B`），用于卡片上的色点提示 */
  accent?: string;
  /** 展示标签（如 `["社招", "技术", "单栏"]`），场景筛选按这里匹配 */
  tags?: string[];
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

/**
 * §2.3 更新简历（`PUT /api/v1/resumes/{resume_id}`）。
 * 至少传一个字段；后端据此重渲染 raw_text / parsed，并返回更新后的 Resume。
 */
export interface UpdateResumePayload {
  title?: string;
  template_code?: string | null;
  content?: EditorContent;
}
