# ZhenResume API 契约 v1（冻结版）

> 本文件是前端与后端并行的唯一接口依据。改动必须先改本文件。
> Base URL（本地）：`http://127.0.0.1:8000`，所有业务接口前缀 `/api/v1`。
> 前端通过 `NEXT_PUBLIC_API_BASE` 读取 Base URL，默认 `http://127.0.0.1:8000`。

## 0. 通用约定

- 请求/响应均为 JSON，`Content-Type: application/json`；上传接口为 `multipart/form-data`。
- 时间字段统一 ISO 8601 字符串（UTC）。
- 成功响应直接返回资源对象，不额外包裹 `data`。
- 错误响应统一结构，HTTP 状态码有意义：

```json
{ "detail": { "code": "RESUME_NOT_FOUND", "message": "简历不存在" } }
```

- 分页参数：`?limit=20&offset=0`；列表响应为 `{ "items": [...], "total": 12 }`。

### 0.1 归属校验与文件下载（重要，客户端必读）

- 除分享场景外，所有接口用请求头 `X-Device-Id` 做匿名用户隔离，**并校验资源归属**：
  简历、JD、任务、报告、批次都只能由创建它的设备访问，否则返回对应的 `*_NOT_FOUND`。
- 因此**文件下载类接口（§2.4 简历导出、§5.2 报告 Markdown、§6.5 CSV 导出）必须用 `fetch` 携带该请求头**，
  不能用 `<a href download>` 或 `window.open`：浏览器的原生导航不会带自定义请求头，
  请求会落到 `anonymous` 用户上，结果就是 404。
- 拿到 `Response` 后自行生成 blob URL 触发下载，文件名从 `Content-Disposition` 解析。
  服务端已用 `Access-Control-Expose-Headers: Content-Disposition` 暴露该响应头，
  否则浏览器端的 JS 读不到它。
- 例外：`GET /reports/{report_id}` 与 `GET /reports/{report_id}/markdown` 允许匿名只读，以支持分享链接（§5.2）。

---

## 1. 健康检查

`GET /api/v1/health` → `200`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "llm_mode": "live",
  "llm_provider": "deepseek",
  "llm_model": "deepseek-chat"
}
```

`llm_mode` ∈ `live` | `mock`。未配置 `DEEPSEEK_API_KEY` 时后端自动进入 `mock`，全链路仍可跑通。

---

## 2. 简历

### 2.1 上传并解析

`POST /api/v1/resumes` — `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | File | 是 | `.pdf` / `.docx` / `.png` / `.jpg` / `.jpeg` / `.txt` |
| `title` | string | 否 | 简历标题，缺省取文件名 |

响应 `201`：

```json
{
  "resume_id": "uuid",
  "title": "张三-后端开发.pdf",
  "file_url": "/static/uploads/2026/02/uuid.pdf",
  "raw_text": "全文纯文本……",
  "parsed": { "…见 §7.1 ParsedResume…" },
  "source": "upload",
  "editor_content": null,
  "created_at": "2026-02-11T08:00:00Z"
}
```

- `source` ∈ `upload` | `editor`，决定报告页「修改后重新诊断」跳回上传页还是编辑器。
- `editor_content`：编辑器创建的简历才有值（结构见 §2.5），上传的简历为 `null`。
  编辑器用它回填表单，因此**同一份简历可以被反复打开修改**。

### 2.2 列表 / 详情 / 删除

- `GET /api/v1/resumes?limit=&offset=` → `{ "items": [ResumeSummary], "total": n }`
  - `ResumeSummary = { resume_id, title, created_at, parsed_name, parsed_title, source }`
  - `source` ∈ `upload` | `editor`，编辑器页的「载入已上传简历」列表据此区分来源。
- `GET /api/v1/resumes/{resume_id}` → 同 2.1 响应结构
- `DELETE /api/v1/resumes/{resume_id}` → `204`（同时级联删除其分析任务与报告；隐私要求，见 aaa.md §4）

### 2.3 编辑器创建简历（不走文件上传）

`POST /api/v1/resumes/from-editor`

```json
{
  "title": "张三-后端开发",
  "template_code": "general_experienced",
  "content": { "…见 §2.5 EditorContent…" }
}
```

- `template_code` 可为 `null`（用户从空白开始）。
- 后端会把结构化内容渲染成纯文本存入 `raw_text`，因此**诊断流水线完全复用**，与上传文件走同一条链路。
- 响应 `201`：与 §2.1 结构相同，另加 `"source": "editor"` 与回显的 `"editor_content"`。

### 2.4 简历导出

`GET /api/v1/resumes/{resume_id}/export/{format}`，`format` ∈ `markdown` | `docx` | `pdf`

- 成功返回文件流，`Content-Disposition: attachment; filename="..."`。
- 对应 `Content-Type`：`text/markdown; charset=utf-8` / `application/vnd.openxmlformats-officedocument.wordprocessingml.document` / `application/pdf`。
- 简历不存在 → `404 RESUME_NOT_FOUND`。
- `format=pdf` 且服务端未配置 LibreOffice → `503 PDF_UNAVAILABLE`，前端应引导改用 DOCX 或浏览器打印。
- 若简历既无编辑器原文、解析结果也为空（无法结构化）→ `400 VALIDATION_ERROR`，提示用户载入编辑器补全。
- 上传的简历同样可导出，但以**解析结果**为渲染来源：**解析没识别到的内容不会出现在导出文件里**。
  要获得无损往返（写进去什么、导出来就是什么），请用编辑器创建简历。

### 2.5 EditorContent 结构

所有键始终存在；无内容时为空字符串或空数组。

```json
{
  "basics": {
    "name": "张三",
    "target_title": "后端开发工程师",
    "phone": "138-0000-0000",
    "email": "zhangsan@example.com",
    "city": "杭州",
    "links": ["github.com/zhangsan"]
  },
  "summary": "3 年后端开发经验，专注交易链路高并发场景。",
  "work": [
    {
      "company": "某科技有限公司",
      "title": "后端开发工程师",
      "start": "2022.07",
      "end": "至今",
      "location": "杭州",
      "highlights": ["负责订单核心接口的设计与开发"]
    }
  ],
  "projects": [
    {
      "name": "订单系统重构",
      "role": "核心开发",
      "start": "2023.03",
      "end": "2023.09",
      "tech": ["Python", "Redis"],
      "highlights": ["主导订单查询接口重构"]
    }
  ],
  "education": [
    {
      "school": "某大学",
      "degree": "本科",
      "major": "计算机科学与技术",
      "start": "2018.09",
      "end": "2022.06"
    }
  ],
  "skills": ["Python", "MySQL", "Redis"],
  "certificates": ["CET-6"],
  "awards": ["校级一等奖学金"],
  "custom_sections": [
    { "title": "开源贡献", "items": ["xxx-gateway：负责限流模块设计与维护"] }
  ]
}
```

`links` / `skills` / `certificates` / `awards` / `highlights` / `tech` / `items` 均为字符串数组；`custom_sections[].title` 为空时该区块不渲染。

---

## 3. JD

### 3.1 创建并解析

`POST /api/v1/jds`

```json
{ "title": "后端开发工程师", "company": "某科技", "raw_text": "岗位职责：……" }
```

响应 `201`：

```json
{
  "jd_id": "uuid",
  "title": "后端开发工程师",
  "company": "某科技",
  "raw_text": "……",
  "parsed": { "…见 §7.2 ParsedJD…" },
  "created_at": "2026-02-11T08:00:00Z"
}
```

- `GET /api/v1/jds?limit=&offset=` → `{ "items": [JdSummary], "total": n }`
- `GET /api/v1/jds/{jd_id}` → 同 3.1 响应结构
- `DELETE /api/v1/jds/{jd_id}` → `204`

---

## 4. 分析任务（工作流）

### 4.1 创建任务

`POST /api/v1/tasks`

```json
{ "resume_id": "uuid", "jd_id": "uuid" }
```

响应 `202`：`{ "task_id": "uuid", "status": "pending" }`

后端在 BackgroundTasks 中跑 8-Agent 工作流，前端轮询 4.2。

### 4.2 任务状态（轮询接口，1.5s 一次）

`GET /api/v1/tasks/{task_id}`

```json
{
  "task_id": "uuid",
  "status": "running",
  "progress": 0.5,
  "current_step": "impact",
  "total_steps": 8,
  "steps": [
    { "step_name": "parse",  "status": "done",    "latency_ms": 820,  "tokens": 0,    "cost": 0.0 },
    { "step_name": "jd_match","status": "running","latency_ms": null, "tokens": null, "cost": null }
  ],
  "report_id": null,
  "error": null
}
```

- `status` ∈ `pending` | `running` | `done` | `failed`
- `progress` ∈ `[0, 1]`
- `step_name` 顺序固定为：`parse`, `jd_match`, `ats`, `impact`, `risk`, `suggestion`, `report`, `qc`
- `report_id` 在 `status=done` 时非空
- `failed` 时 `error` 为非空字符串

### 4.3 任务列表

`GET /api/v1/tasks?limit=&offset=` → `{ "items": [TaskSummary], "total": n }`

`TaskSummary = { task_id, resume_id, jd_id, status, created_at, report_id }`

---

## 5. 报告

### 5.1 获取报告

`GET /api/v1/reports/{report_id}` → 完整报告对象：

```json
{
  "report_id": "uuid",
  "task_id": "uuid",
  "resume_id": "uuid",
  "resume_title": "张三-后端开发.pdf",
  "resume_source": "upload",
  "has_editor_content": false,
  "jd_title": "后端开发工程师",
  "created_at": "2026-02-11T08:01:00Z",
  "scores": { "overall": 78, "ats": 82, "impact": 66, "clarity": 85, "match": 74, "risk": 88 },
  "risk_level": "yellow",
  "summary": "整体与目标岗位匹配度中等……",
  "findings": [
    {
      "finding_id": "uuid",
      "category": "impact",
      "severity": "red",
      "title": "工作经历缺少量化结果",
      "original_text": "负责后端接口开发与维护",
      "issue": "只描述职责，没有说明业务规模与产出结果，HR 无法判断贡献量级。",
      "evidence": {
        "source": "resume",
        "quote": "负责后端接口开发与维护",
        "span": [412, 424],
        "field": "work[0].highlights[0]"
      },
      "suggestion": "补充接口承载的 QPS、数据量或稳定性指标。",
      "rewritten_text": "负责订单核心接口开发与维护，支撑日均 200 万次调用，P99 延迟从 480ms 降至 120ms。"
    }
  ],
  "missing_keywords": ["Kubernetes", "消息队列"],
  "action_items": ["为最近两段经历各补 2 条量化成果", "在技能栏补充 K8s 相关实践"],
  "knowledge_refs": [
    { "doc_id": "uuid", "chunk_id": "uuid", "title": "后端工程师能力模型", "scope": "role" }
  ],
  "disclaimer": "本报告由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。"
}
```

字段约束（前端可依赖）：

- `scores.*` 为 0–100 整数；`risk` 分越高表示风险越低。
- `risk_level` ∈ `red` | `yellow` | `green`，由 `scores.risk` 派生：`≥80 green`，`60–79 yellow`，`<60 red`。
- `severity` ∈ `red` | `yellow` | `green`；`category` ∈ `ats` | `impact` | `clarity` | `risk` | `gap`。
- `findings` 至少 5 条（验收标准，见 aaa.md §5），按 severity 排序：red → yellow → green。
- 每条 `severity=red` 的 finding 必有非空 `evidence.quote`，且该 quote 必须能在 `raw_text` 中定位（证据链要求）。
- `evidence.source` ∈ `resume` | `jd` | `knowledge`。

### 5.2 导出与分享

- `GET /api/v1/reports/{report_id}/markdown` → `text/markdown`，下载文件名 `zhenresume-report-{report_id}.md`
- `GET /api/v1/reports/{report_id}/share` → `{ "share_url": "http://127.0.0.1:3000/share/{report_id}", "expires_at": null }`

---

## 6. 其他接口

### 6.1 定价与支付

- `GET /api/v1/plans` → `{ "items": [Plan] }`

```json
{ "code": "single_deep", "name": "单份深度报告", "price": 19.9, "currency": "CNY",
  "period": "once", "quota": 1, "features": ["完整维度评分", "逐条改写建议", "PDF 导出"] }
```

`period` ∈ `once` | `month`。套餐 code 固定集合：`free`, `single_basic`, `single_deep`, `monthly_basic`, `monthly_pro`, `hr_basic`, `hr_pro`。

- `POST /api/v1/payments/orders` `{ "plan_code": "single_deep", "provider": "stripe" }` → `201`

```json
{ "order_no": "ZR2026021100001", "plan_code": "single_deep", "amount": 19.9,
  "currency": "CNY", "provider": "stripe", "status": "pending",
  "pay_url": "https://example.com/mock-pay/ZR2026021100001" }
```

MVP 阶段 `pay_url` 为 Mock 支付页；`provider` ∈ `stripe` | `wechat` | `alipay`。

- `POST /api/v1/payments/callback` `{ "order_no": "...", "status": "paid" }` → `{ "ok": true, "order_no": "...", "status": "paid" }`（幂等）

### 6.2 知识库 RAG

- `POST /api/v1/knowledge/docs` `{ "scope": "role", "role_family": "backend", "doc_type": "skill_model", "title": "...", "content": "..." }` → `201 KnowledgeDoc`
- `GET /api/v1/knowledge/docs?scope=&limit=&offset=` → `{ "items": [KnowledgeDoc], "total": n }`
- `POST /api/v1/knowledge/search` `{ "query": "后端 高并发", "top_k": 5, "scope": "role" }` → `{ "items": [KnowledgeHit] }`

```json
{ "chunk_id": "uuid", "doc_id": "uuid", "title": "后端工程师能力模型",
  "scope": "role", "score": 0.83, "text": "……切片正文……" }
```

`scope` ∈ `role` | `industry` | `resume_template` | `compliance`。

### 6.3 管理后台

- `GET /api/v1/admin/metrics` →

```json
{
  "tasks_total": 42, "tasks_done": 40, "tasks_failed": 2,
  "reports_total": 40, "avg_latency_ms": 21000,
  "tokens_total": 512000, "cost_total": 6.42, "cost_per_report": 0.16,
  "feedback_avg_rating": 4.3, "fairness_pass_rate": 1.0,
  "step_breakdown": [ { "step_name": "ats", "calls": 40, "avg_latency_ms": 2100, "cost": 0.9 } ]
}
```

- `GET /api/v1/admin/prompts` → `{ "items": [ { "name": "ats_agent", "version": 3, "updated_at": "..." } ] }`
- `POST /api/v1/admin/prompts` `{ "name": "ats_agent", "content": "..." }` → `201`（版本号自增）
- `GET /api/v1/admin/feedback?limit=&offset=` → `{ "items": [Feedback], "total": n }`

### 6.4 用户反馈

`POST /api/v1/feedback` `{ "report_id": "uuid", "rating": 5, "comment": "很有用" }` → `201`

### 6.5 HR 批量初筛

- `POST /api/v1/hr/batches` `{ "jd_id": "uuid", "resume_ids": ["uuid", "..."] }` → `202 { "batch_id": "uuid", "status": "pending", "total": n }`
- `GET /api/v1/hr/batches/{batch_id}` →

```json
{ "batch_id": "uuid", "status": "running", "total": 20, "finished": 7,
  "items": [ { "resume_id": "uuid", "resume_title": "李四.pdf", "status": "done",
               "overall_score": 81, "match_score": 84, "risk_level": "green",
               "report_id": "uuid", "summary": "……" } ] }
```

- 列表按 `overall_score` 降序；未完成的条目 `status="pending"` 且分数为 `null`。
- `GET /api/v1/hr/batches/{batch_id}/export.csv` → `text/csv`

### 6.6 简历模板

`GET /api/v1/templates` → `{ "items": [TemplateSummary] }`

```json
{
  "code": "general_experienced",
  "name": "通用社招模板",
  "description": "适合有 2 年以上正式工作经验的社招投递，突出成果与量化",
  "target": "experienced",
  "role_family": "general",
  "section_count": 7
}
```

`target` ∈ `campus`（应届） | `experienced`（社招） | `transition`（转行）。

`GET /api/v1/templates/{code}` → TemplateDetail

```json
{
  "code": "general_experienced",
  "name": "通用社招模板",
  "description": "…",
  "target": "experienced",
  "role_family": "general",
  "section_count": 7,
  "intro": "这份模板的适用前提与使用顺序说明",
  "writing_rules": ["每条要点用强动词开头，结构为「动作 + 对象 + 量化结果」", "…"],
  "sections": [
    {
      "key": "basics",
      "title": "个人信息",
      "required": true,
      "tip": "只写姓名、目标岗位、手机、邮箱、城市与作品集链接，不要写身份证号、婚育状况。",
      "examples": ["张三 | 后端开发工程师 | 138-0000-0000 | z@example.com | 现居杭州"],
      "max_items": null
    }
  ],
  "example_content": { "…一份填好的 §2.5 EditorContent，供一键载入…" }
}
```

- `sections[].key` ∈ `basics` | `summary` | `work` | `projects` | `education` | `skills` | `certificates` | `awards` | `custom`。
- `max_items` 为 `null` 表示该区块条目数不限。
- 模板是**代码常量**（与 §6.1 套餐同理）：结构变更属于发版行为，避免运行期数据漂移。
- 模板不存在 → `404 TEMPLATE_NOT_FOUND`。

---

## 7. 结构化 Schema

### 7.1 ParsedResume

```json
{
  "basics": { "name": "张三", "email": "z@x.com", "phone": "138…", "city": "杭州", "links": ["github.com/z"] },
  "education": [ { "school": "某大学", "degree": "本科", "major": "计算机", "start": "2018-09", "end": "2022-06", "highlights": [] } ],
  "work": [ { "company": "某公司", "title": "后端开发", "start": "2022-07", "end": "至今", "location": "杭州", "highlights": ["负责订单核心接口开发与维护"] } ],
  "projects": [ { "name": "订单系统重构", "role": "核心开发", "start": "2023-03", "end": "2023-09", "highlights": [], "tech": ["Python", "MySQL"] } ],
  "skills": ["Python", "MySQL", "Redis"],
  "certificates": ["CET-6"],
  "timeline": [ { "start": "2018-09", "end": "2022-06", "label": "某大学 本科", "type": "education" } ]
}
```

所有键始终存在；无内容时为空数组或空字符串（前端不要判 `undefined`）。

### 7.2 ParsedJD

```json
{
  "title": "后端开发工程师",
  "company": "某科技",
  "seniority": "3-5年",
  "experience_years": 3,
  "education": "本科及以上",
  "skills_core": ["Python", "MySQL", "Redis"],
  "skills_bonus": ["Kubernetes"],
  "responsibilities": ["负责订单核心服务设计与开发"],
  "keywords": ["高并发", "微服务", "分布式"]
}
```

`experience_years` 可为 `null`。

---

## 8. 错误码表

| code | HTTP | 含义 |
|---|---|---|
| `VALIDATION_ERROR` | 400 | 入参不合法 |
| `UNSUPPORTED_FILE_TYPE` | 415 | 简历文件类型不支持 |
| `FILE_TOO_LARGE` | 413 | 超过 10MB |
| `RESUME_NOT_FOUND` | 404 | 简历不存在 |
| `JD_NOT_FOUND` | 404 | JD 不存在 |
| `TASK_NOT_FOUND` | 404 | 任务不存在 |
| `REPORT_NOT_FOUND` | 404 | 报告不存在 |
| `BATCH_NOT_FOUND` | 404 | 批次不存在 |
| `PLAN_NOT_FOUND` | 404 | 套餐不存在 |
| `TEMPLATE_NOT_FOUND` | 404 | 简历模板不存在 |
| `PDF_UNAVAILABLE` | 503 | 服务端未配置 PDF 转换组件（LibreOffice） |
| `QUOTA_EXCEEDED` | 402 | 免费次数已用完 |
| `LLM_ERROR` | 502 | LLM 调用失败且降级不可用 |
| `INTERNAL_ERROR` | 500 | 未预期错误 |

---

## 9. 页面与接口对应关系（前端实现依据）

| 页面 | 路由 | 使用接口 |
|---|---|---|
| 首页 | `/` | 2.1 上传简历、3.1 创建 JD、4.1 创建任务、6.1 套餐 |
| 简历编辑器 | `/editor?resumeId=&template=` | 6.6 模板、2.3 编辑器创建简历、2.4 导出、3.1 创建 JD、4.1 创建任务、2.2 载入已上传简历 |
| 进度页 | `/analyze/[taskId]` | 4.2 轮询 |
| 报告页 | `/report/[reportId]` | 5.1 获取报告、5.2 markdown 导出 |
| 分享页 | `/share/[reportId]` | 5.1（只读，隐藏操作区） |
| HR 批量 | `/hr` | 3.1、2.2、6.5 |
| 管理后台 | `/admin` | 6.3 |
| 定价 | `/pricing` | 6.1、6.1 下单 |

**闭环约定**：报告页的「修改后重新诊断」行为由报告的 `resume_source` 决定 ——
`editor` 时跳 `/editor?resumeId={resume_id}` 并回填原内容；`upload` 时跳首页重新上传。
编辑器内的「发起诊断」走 §2.3 创建简历 → §3.1 创建 JD → §4.1 创建任务，成功后跳 `/analyze/{task_id}`。

**编辑器内的文档导入**复用 §2.1 的文件上传（不新增接口）：上传拿回 `parsed` 后，
用编辑器侧的映射函数填入表单。注意此时应**丢弃该上传件的 `resume_id`** ——
上传件没有 `editor_content`，导出会退化为「以解析结果为源」的有损渲染；
置空后，用户下一次导出或发起诊断时会由 §2.3 另存为一份带完整结构化内容的可编辑版本。
