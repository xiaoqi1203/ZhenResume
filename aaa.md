以下为 **ZhenResume（简甄）V1.0 启动文档包**。定位：AI 简历诊断与优化工作流，面向求职者与中小企业 HR，核心能力是“RAG 知识库 + 多 Agent 工作流 + 可追溯证据链”。

---

# 一、需求规格说明书（SRS）

## 1. 引言

### 1.1 目的
本文档定义 ZhenResume 的产品范围、功能需求、非功能需求与验收标准，作为产品、设计、开发、测试的统一依据。

### 1.2 产品定位
**ZhenResume（简甄）**：60 秒诊断简历，让每段经历都被看见。  
不是简历打分器，而是给出**风险标注、匹配分析、证据引用、可直接复制的改写建议**的工作流工具。

### 1.3 目标用户
- **C 端求职者**：应届生、跳槽者、转行者、自由职业者。
- **B 端中小企业 HR / 招聘负责人**：无预算买企业级 ATS，但需要快速初筛。
- **管理员**：维护知识库、Prompt 模板、审核反馈、监控成本。

### 1.4 术语
| 术语 | 说明 |
|---|---|
| JD | 岗位描述 |
| ATS | 申请人跟踪系统 |
| RAG | 检索增强生成 |
| Agent | 执行特定任务的工作流节点 |
| 证据链 | 每条建议可追溯到简历原文或 JD 原文 |
| 报告 | 结构化诊断结果，含评分、风险、改写建议 |

## 2. 总体描述

### 2.1 产品目标
- 帮求职者提升简历通过率与面试转化率。
- 帮 HR 把初筛时间从小时级降到分钟级。
- 建立可复用的岗位知识库与优质简历范式库。

### 2.2 核心价值
- **证据化**：不黑箱打分，每条结论有原文依据。
- **可执行**：直接给改写句子，不给泛泛建议。
- **工作流化**：解析 → 匹配 → 评分 → 风险 → 建议 → 质检 → 报告。
- **越用越准**：用户反馈与人工修正沉淀为知识资产。

### 2.3 假设与依赖
- 用户可上传 PDF / Word / 图片简历。
- LLM API 可用，成本可控。
- 支付渠道可用。
- 简历与 JD 数据涉及隐私，需加密与脱敏。

## 3. 功能需求

### 3.1 简历解析与结构化
- 支持 PDF、DOCX、PNG、JPG。
- OCR 识别扫描件。
- 抽取字段：基本信息、教育、工作经历、项目、技能、证书、时间线。
- 输出 JSON 结构，保留原文位置。

### 3.2 JD 解析
- 支持粘贴文本或上传 JD。
- 抽取：岗位名称、核心技能、经验要求、学历要求、加分项、关键词。
- 输出结构化 JSON。

### 3.3 多维度分析
- **ATS 匹配**：关键词覆盖、格式可解析性。
- **影响力评分**：是否量化成果，是否使用强动词。
- **清晰度评分**：结构、时态、长度、可读性。
- **风险检测**：时间线断层、技能与项目矛盾、夸大表述。
- **竞争力差距**：与同岗位基准画像对比。

### 3.4 报告生成
- 总分 + 维度分。
- 红 / 黄 / 绿风险标注。
- 逐条问题：原文、问题、证据、建议、改写句。
- 导出 PDF / 分享链接。
- 支持“修改后重新诊断”。

### 3.5 知识库 RAG
- 岗位知识库：技能模型、项目经验、职级要求。
- 优质简历范式库：各行业优秀表达。
- 法规与公平性知识：避免歧视性建议。
- 检索结果带引用来源。

### 3.6 工作流 Agent
- 解析 Agent、JD 匹配 Agent、ATS Agent、影响力 Agent、风险 Agent、建议 Agent、报告合成 Agent、质检 Agent。
- 每个 Agent 独立可观测：输入、输出、耗时、Token、成本。
- 支持失败重试与降级。

### 3.7 支付与订阅
- 免费：3 次基础诊断。
- 按次：9.9 元 / 19.9 元。
- 月订阅：39 元 / 99 元。
- HR 套餐：999 元 / 2999 元 / 月。
- 支持 Stripe、微信支付、支付宝。

### 3.8 用户中心
- 注册 / 登录、历史报告、简历管理、JD 管理、订单、发票、反馈。

### 3.9 HR 批量筛选
- 上传 JD + 批量简历。
- 输出排序列表、匹配分、风险标注、简版报告。
- 导出 CSV / PDF。

### 3.10 管理后台
- 知识库维护、Prompt 模板管理、用户反馈审核、成本监控、敏感词与公平性检查。

## 4. 非功能需求

| 类别 | 要求 |
|---|---|
| 性能 | 单份简历诊断 ≤ 60 秒；批量 100 份 ≤ 10 分钟 |
| 可用性 | 99.5% 可用；支持重试与降级 |
| 安全 | HTTPS、数据加密、权限隔离、审计日志 |
| 隐私 | 用户可删除数据；简历默认不用于训练 |
| 公平性 | 不输出性别、年龄、婚育等歧视性建议 |
| 可扩展 | 支持多模型路由、多语言、多行业 |
| 成本 | 单份报告 API 成本 ≤ 0.5 元 |
| 合规 | 明确“不构成录用决策，不构成法律意见” |

## 5. 验收标准
- 上传简历 + JD 后 60 秒内生成报告。
- 报告含至少 5 条可执行改写建议。
- 每条红色风险有原文引用与证据。
- 支付成功后报告可下载。
- 用户可删除数据。
- 公平性检查通过率 100%。

---

# 二、用户流程表

## 1. 求职者流程

| 步骤 | 角色 | 操作 | 系统响应 | 输出 |
|---|---|---|---|---|
| 1 | 求职者 | 打开网站 | 展示首页与定价 | 首页 |
| 2 | 求职者 | 注册/登录 | 创建账户 | 用户中心 |
| 3 | 求职者 | 上传简历 | 解析文件 | 结构化简历 |
| 4 | 求职者 | 粘贴目标 JD | 解析 JD | 结构化 JD |
| 5 | 求职者 | 选择套餐 | 创建订单 | 支付页 |
| 6 | 求职者 | 支付 | 回调确认 | 任务启动 |
| 7 | 系统 | 执行 Agent 工作流 | 多步分析 | 中间结果 |
| 8 | 系统 | 生成报告 | 合成 PDF | 报告页 |
| 9 | 求职者 | 查看报告 | 展示评分与建议 | 可下载 |
| 10 | 求职者 | 按建议修改 | 重新上传 | 二次诊断 |
| 11 | 求职者 | 反馈 | 记录反馈 | 知识沉淀 |

## 2. HR 流程

| 步骤 | 角色 | 操作 | 系统响应 | 输出 |
|---|---|---|---|---|
| 1 | HR | 注册企业账户 | 创建组织 | 企业后台 |
| 2 | HR | 上传 JD | 解析 | JD 模板 |
| 3 | HR | 批量上传简历 | 解析队列 | 简历池 |
| 4 | HR | 启动初筛 | 批量 Agent | 排序列表 |
| 5 | HR | 查看候选人 | 展示匹配分与风险 | 候选人卡片 |
| 6 | HR | 导出报告 | 生成 CSV/PDF | 初筛报告 |
| 7 | HR | 标记面试/淘汰 | 记录决策 | 数据沉淀 |

## 3. 管理员流程

| 步骤 | 角色 | 操作 | 系统响应 | 输出 |
|---|---|---|---|---|
| 1 | 管理员 | 登录后台 | 权限验证 | 管理面板 |
| 2 | 管理员 | 维护知识库 | 上传/编辑/向量化 | 知识库更新 |
| 3 | 管理员 | 管理 Prompt | 版本控制 | 模板更新 |
| 4 | 管理员 | 审核反馈 | 查看/修正 | 训练样本 |
| 5 | 管理员 | 监控成本 | 看板 | 成本报表 |
| 6 | 管理员 | 公平性检查 | 扫描输出 | 合规报告 |

---

# 三、数据库设计

> 数据库：PostgreSQL + pgvector。以下为核心表 DDL 草案。

```sql
-- 用户
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE,
  phone VARCHAR(32) UNIQUE,
  password_hash VARCHAR(255),
  role VARCHAR(20) DEFAULT 'jobseeker', -- jobseeker, hr, admin
  org_id UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 组织
CREATE TABLE organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255),
  plan VARCHAR(50),
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 简历
CREATE TABLE resumes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  title VARCHAR(255),
  file_url TEXT,
  raw_text TEXT,
  parsed_json JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- JD
CREATE TABLE job_descriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  title VARCHAR(255),
  company VARCHAR(255),
  raw_text TEXT,
  parsed_json JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 分析任务
CREATE TABLE analysis_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  resume_id UUID REFERENCES resumes(id),
  jd_id UUID REFERENCES job_descriptions(id),
  type VARCHAR(20), -- single, batch
  status VARCHAR(20) DEFAULT 'pending', -- pending, running, done, failed
  created_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- 报告
CREATE TABLE analysis_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID REFERENCES analysis_tasks(id),
  overall_score INT,
  ats_score INT,
  impact_score INT,
  clarity_score INT,
  match_score INT,
  risk_level VARCHAR(10),
  summary TEXT,
  report_url TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 发现项
CREATE TABLE report_findings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_id UUID REFERENCES analysis_reports(id),
  category VARCHAR(50), -- ats, impact, clarity, risk, gap
  severity VARCHAR(10), -- red, yellow, green
  original_text TEXT,
  issue TEXT,
  suggestion TEXT,
  rewritten_text TEXT,
  evidence_ref JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 知识库文档
CREATE TABLE knowledge_docs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope VARCHAR(50), -- role, industry, resume_template, compliance
  role_family VARCHAR(100),
  doc_type VARCHAR(50),
  title VARCHAR(255),
  content TEXT,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 知识库切片与向量
CREATE TABLE knowledge_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id UUID REFERENCES knowledge_docs(id),
  chunk_text TEXT,
  embedding VECTOR(1536),
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 工作流步骤
CREATE TABLE workflow_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID REFERENCES analysis_tasks(id),
  step_name VARCHAR(100),
  status VARCHAR(20),
  input_json JSONB,
  output_json JSONB,
  tokens INT,
  cost NUMERIC(10,4),
  latency_ms INT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 支付
CREATE TABLE payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  order_no VARCHAR(64) UNIQUE,
  amount NUMERIC(10,2),
  currency VARCHAR(10),
  status VARCHAR(20),
  provider VARCHAR(20),
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 订阅
CREATE TABLE subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  plan VARCHAR(50),
  status VARCHAR(20),
  start_at TIMESTAMPTZ,
  end_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 反馈
CREATE TABLE feedbacks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_id UUID REFERENCES analysis_reports(id),
  user_id UUID REFERENCES users(id),
  rating INT,
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 审计日志
CREATE TABLE audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  action VARCHAR(100),
  target VARCHAR(255),
  ip VARCHAR(64),
  created_at TIMESTAMPTZ DEFAULT now()
);
```

---

# 四、技术栈

| 层级 | 技术选型 | 说明 |
|---|---|---|
| 前端 | Next.js 14 + TypeScript + Tailwind + shadcn/ui | 快速出 MVP，SEO 友好 |
| 后端 | FastAPI + Python | AI/Agent 生态成熟 |
| Agent 编排 | LangGraph | 多步骤、状态管理、可观测 |
| RAG | LlamaIndex + pgvector | 检索、重排、引用 |
| LLM 路由 | Claude 3.5 Sonnet / GPT-4o / DeepSeek-V3 | 按任务与成本路由 |
| 数据库 | PostgreSQL + pgvector | 业务数据 + 向量 |
| 缓存/队列 | Redis + Celery | 异步任务、重试 |
| 对象存储 | S3 / Supabase Storage | 简历与报告文件 |
| 支付 | Stripe + 微信支付 + 支付宝 | 国内外覆盖 |
| 部署 | Vercel + Railway / Fly.io / AWS ECS | 前端 Vercel，后端容器 |
| 监控 | Langfuse + Sentry + PostHog | LLM 可观测、错误、行为 |
| 认证 | Clerk / Supabase Auth | 快速接入 |
| 邮件 | Resend / SendGrid | 通知与报告 |

---

# 五、概要设计

## 1. 系统分层

```
接入层：Web / 移动端 / API
应用层：用户、简历、JD、报告、支付、HR 批量
Agent 编排层：LangGraph 工作流
RAG 层：LlamaIndex + pgvector + 重排
数据层：PostgreSQL、Redis、S3
基础设施：Vercel、Railway、Langfuse、Sentry
```

## 2. 核心模块

- **用户模块**：认证、权限、组织。
- **简历模块**：上传、解析、结构化、版本。
- **JD 模块**：粘贴、解析、关键词。
- **分析模块**：任务创建、Agent 调度、状态跟踪。
- **报告模块**：合成、导出、分享。
- **知识库模块**：文档、切片、向量、检索。
- **支付模块**：订单、订阅、回调。
- **HR 模块**：批量上传、排序、导出。
- **管理后台**：知识库、Prompt、反馈、成本。

## 3. Agent 工作流

```
解析 Agent → JD 匹配 Agent → ATS Agent → 影响力 Agent
→ 风险 Agent → 建议 Agent → 报告合成 Agent → 质检 Agent
```

每个 Agent 输出 JSON，质检 Agent 检查：
- 是否有原文依据。
- 是否含歧视性内容。
- 是否给出可执行建议。
- 是否引用知识库来源。

## 4. 数据流

1. 用户上传简历 + JD。
2. 解析服务抽取文本与结构。
3. 任务入队，LangGraph 启动。
4. RAG 检索岗位知识库与简历范式库。
5. 多 Agent 依次分析。
6. 报告合成，写入数据库与 S3。
7. 通知用户，展示报告。
8. 反馈沉淀，更新知识库。

## 5. 部署架构

- 前端：Vercel。
- 后端：Railway / Fly.io 容器。
- 数据库：Supabase / Neon PostgreSQL。
- 队列：Redis Cloud。
- 文件：S3 / Supabase Storage。
- LLM：API 调用，多供应商路由。
- 监控：Langfuse + Sentry。

---

# 六、产品设计核心设计

## 1. 产品原则

1. **证据优先**：每条建议必须能追溯到简历原文或 JD。
2. **可执行**：直接给改写句，不给空泛建议。
3. **不替用户决策**：只做诊断与建议，不做录用决策。
4. **公平性**：不输出性别、年龄、婚育、地域等歧视性内容。
5. **越用越准**：反馈与修正沉淀为知识资产。

## 2. 报告结构

```
1. 总分与维度分
   - 综合分、ATS 匹配、影响力、清晰度、竞争力差距
2. 红黄绿风险概览
3. 逐条发现
   - 原文
   - 问题
   - 证据
   - 建议
   - 改写句（可直接复制）
4. 缺失关键词与建议补充
5. 下一步行动清单
6. 免责声明
```

## 3. 评分维度

| 维度 | 权重 | 说明 |
|---|---|---|
| ATS 匹配 | 30% | 关键词、格式、可解析性 |
| 影响力 | 25% | 量化成果、强动词 |
| 清晰度 | 20% | 结构、时态、长度 |
| 竞争力差距 | 15% | 与基准画像对比 |
| 风险 | 10% | 时间线、矛盾、夸大 |

## 4. 交互设计

- 首页：上传即分析，无需注册可试。
- 进度页：展示 Agent 步骤，降低等待焦虑。
- 报告页：左原文、右建议，可一键复制。
- 二次诊断：修改后重新上传，对比分数变化。
- 导出：PDF、分享链接。
- HR 端：表格排序、筛选、批量导出。

## 5. 定价设计

| 套餐 | 价格 | 内容 |
|---|---|---|
| 免费 | 0 元 | 3 次基础诊断 |
| 按次 | 9.9 / 19.9 元 | 单份深度报告 |
| 月订阅 | 39 / 99 元 | 不限次 + 历史对比 |
| HR 基础 | 999 元/月 | 100 份/月 + 批量排序 |
| HR 专业 | 2999 元/月 | 500 份/月 + API + 定制知识库 |

## 6. 冷启动策略

- 内容获客：发布“简历避坑指南”“ATS 关键词清单”。
- 社群：求职群、独立开发者群、HR 社群。
- 免费报告换反馈：前 100 名用户免费换反馈。
- 合作：与职业规划师、猎头、高校就业中心合作。

## 7. 成功指标

- 付费转化率 ≥ 5%。
- 报告完成率 ≥ 80%。
- 二次诊断率 ≥ 30%。
- NPS ≥ 40。
- 单份报告 API 成本 ≤ 0.5 元。
- 公平性检查通过率 100%。

---

以上为 **ZhenResume（简甄）V1.0 启动文档包**。建议下一步：先用手工 Prompt 跑 10 份真实简历 + JD，验证报告质量与付费意愿，再进入开发。