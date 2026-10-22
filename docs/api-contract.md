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
  服务端已用 `Access-Control-Expose-Headers: Content-Disposition, X-Page-Count, X-Fit-Scale`
  暴露这些响应头，否则浏览器端的 JS 读不到它们（页数/缩放提示会静默降级）。
- **登录态**：请求头 `Authorization: Bearer <jwt>` 优先于 `X-Device-Id`。登录/注册/验证码登录成功时，
  后端会把同一 `X-Device-Id` 匿名用户名下的简历 / JD / 分析任务 / HR 批次 / 反馈迁移到账号
  （幂等；不迁移支付数据），因此匿名期间创建的数据在登录后仍然可见。
- 例外：`GET /reports/{report_id}` 与 `GET /reports/{report_id}/markdown` 允许匿名只读，以支持分享链接（§5.2）。

---

## 1. 健康检查

`GET /api/v1/health` → `200`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "llm_mode": "live",
  "llm_provider": "aliyun",
  "llm_model": "qwen3.8-max",
  "llm_chain": ["aliyun:qwen3.8-max", "aliyun:qwen3.7-flash", "deepseek:deepseek-flash"]
}
```

`llm_mode` ∈ `live` | `mock`。**未配置任何 LLM Key**（`ALIYUN_API_KEY` / `DEEPSEEK_API_KEY`）时后端自动进入 `mock`，全链路仍可跑通。

- `llm_provider` / `llm_model`：当前生效的**第一层**（`llm_mode=mock` 时为 `mock` / `mock-rule-engine`）。
- `llm_chain`（**v1.1 新增，向前兼容**）：按顺序尝试的完整降级链，`供应商:模型`。同一供应商内按模型阶梯降级，供应商之间按 `LLM_PROVIDERS` 顺序降级；全部失败才回落到各 Agent 的规则引擎实现（对应步骤标记 `degraded`）。
- `embedding_provider` / `embedding_model` / `embedding_dim`（**v1.1 新增，向前兼容**）：当前检索向量来源。`provider=hash` 时 `embedding_model` 为 `local-hash`（本地确定性哈希向量）。**这三项与知识库中已存的向量必须一致**，否则检索恒为 0 分（`cosine()` 对长度不一致的向量返回 0）。

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
  "template_code": null,
  "created_at": "2026-02-11T08:00:00Z"
}
```

- `source` ∈ `upload` | `editor`，决定报告页「修改后重新诊断」跳回上传页还是编辑器。
- `editor_content`：编辑器创建的简历才有值（结构见 §2.5），上传的简历为 `null`。
  编辑器用它回填表单，因此**同一份简历可以被反复打开修改**。
- `template_code`（编辑器简历才有值）：创建时选择的模板 code；重新打开编辑器时用它恢复模板提示。
  上传的简历恒为 `null`。

### 2.2 列表 / 详情 / 删除

- `GET /api/v1/resumes?limit=&offset=` → `{ "items": [ResumeSummary], "total": n }`
  - `ResumeSummary = { resume_id, title, created_at, parsed_name, parsed_title, source, template_code }`
  - `source` ∈ `upload` | `editor`，编辑器页的「载入已上传简历」与账号中心「我的简历」据此区分来源。
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

### 2.3.1 更新编辑器简历（原地更新，不再重复新建）

`PUT /api/v1/resumes/{resume_id}`

```json
{
  "title": "张三-后端开发（更新版）",
  "template_code": "general_experienced",
  "content": { "…见 §2.5 EditorContent…" }
}
```

- 三个字段都可选，但**至少提供一个**；只传 `title` 即重命名。
- `content` 提供时必须是“有意义的内容”（`EditorContent.is_meaningful()`），否则 `400 VALIDATION_ERROR`；
  后端重算 `raw_text` / `parsed`，同步更新 `size_bytes`，并保持 `source="editor"`。
- `template_code` 显式传 `null` 表示清空模板；**不传则保持原值**（只更新内容时模板不会丢）。
- 响应 `200`：同 §2.1 结构（含 `template_code` 与 `editor_content`）。
- 非本人简历 → `404 RESUME_NOT_FOUND`（不区分“不存在”与“不是你的”）。
- 前端保存策略：有 `resume_id` 走 PUT，没有才走 §2.3 POST；`resume_id` 随草稿持久化，避免“改一次多一份”。

### 2.4 简历导出

`GET /api/v1/resumes/{resume_id}/export/{format}`，`format` ∈ `markdown` | `docx` | `pdf`

- 成功返回文件流，`Content-Disposition: attachment; filename="..."`。
- 对应 `Content-Type`：`text/markdown; charset=utf-8` / `application/vnd.openxmlformats-officedocument.wordprocessingml.document` / `application/pdf`。
- 简历不存在 → `404 RESUME_NOT_FOUND`。
- `format=pdf` 且服务端未配置 LibreOffice → `503 PDF_UNAVAILABLE`，前端应引导改用 DOCX 或浏览器打印。
- 仅 `format=pdf` 支持可选查询参数 `fit=one`：逐档缩小字号与间距压到 1 页 A4，
  实际缩放比例与页数通过响应头 `X-Fit-Scale` / `X-Page-Count` 返回（任何格式都会带 `X-Page-Count`，PDF 才有意义）。
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

**v1.1 新增（向前兼容）**：响应还包含 `mode`（`fixed`|`agentic`）、`created_at`、`completed_at` 与 `usage`：

```json
{ "usage": { "latency_ms": 47500, "tokens": 13000, "cost": 0.06,
             "steps_cost": 0.06, "evidence_cost": 0.0, "evidence_tokens": 0, "evidence_latency_ms": 0 } }
```

### 4.3 任务列表

`GET /api/v1/tasks?limit=&offset=` → `{ "items": [TaskSummary], "total": n }`

`TaskSummary = { task_id, resume_id, jd_id, status, created_at, report_id }`

---

### 4.4 任务进度流（SSE，推荐；4.2 轮询仍然保留）

`GET /api/v1/tasks/{task_id}/stream` → `text/event-stream`

**为什么加**：一份求职者完整报告实测约 47.5s（优化后），前端 1.5s 轮询在这段时间里
只能显示"进行中"，用户什么都看不到。SSE 让每个步骤一完成就推送，解析完（约 13s）
用户就能看到第一批结论。

**契约要点**

| 项 | 约定 |
|---|---|
| 认证 | 同其它接口（`X-Device-Id` 请求头） |
| 客户端 | ⚠️ **必须用 `fetch()` + `ReadableStream` 读取，不能用 `EventSource`** —— `EventSource` 无法自定义请求头 |
| 归属校验 | 非本人任务返回 `TASK_NOT_FOUND`（与 4.2 一致，不区分"不存在"和"不是你的"） |
| 心跳 | 每约 10s 一行 `: ping`（注释行，不产生事件），用于防代理断连 |
| 服务端轮询 | 500ms 一次读 DB（Celery 模式下任务在别的进程，进程内事件拿不到，只能以 DB 为唯一真源） |
| 上限 | 900s 后主动发 `error` 并关闭（避免连接泄漏）；此时前端应回落到 4.2 轮询 |
| 兼容 | §4.2 轮询**不删除**，老客户端与断线续取继续可用 |

**事件模型**

| event | data | 何时发 |
|---|---|---|
| `step_done` | 单个步骤对象（与 4.2 的 `steps[]` 元素同构：`step_name/status/latency_ms/tokens/cost/notes`） | 某步骤从 `pending` → `done`/`degraded`，每个步骤只发一次 |
| `progress` | `{task_id, status, progress, current_step, total_steps}` | 状态/当前步骤/进度发生变化时 |
| `done` | 完整任务详情（**与 4.2 响应同构**，含 `report_id`） | 任务成功；随后服务端关闭连接 |
| `error` | `{message, task_id?}` | 任务失败、任务不存在、或超过 900s |

**前端用法（伪代码）**

```ts
const res = await fetch(`${API}/api/v1/tasks/${id}/stream`, {
  headers: { 'X-Device-Id': deviceId },
});
const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buf = '';
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  const frames = buf.split('\n\n');
  buf = frames.pop() ?? '';               // 最后一段可能不完整，留到下一轮
  for (const frame of frames) {
    const event = /^event: (.+)$/m.exec(frame)?.[1];
    const data = JSON.parse(/^data: (.+)$/m.exec(frame)?.[1] ?? '{}');
    // step_done → 追加渲染这一段；done → 用 data 直接渲染完整报告
  }
}
```
> 注意 `buf` 要**跨 chunk 保留**：一个 SSE 帧可能被 TCP 拆成两次 `read()`，
> 直接对每个 chunk 做 `JSON.parse` 会偶发解析失败（这也是不能用 `EventSource` 之外
> 图省事手写解析的坑之一）。



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
- `GET /api/v1/reports/{report_id}/share`（也接受 `POST`）→

```json
{ "share_url": "http://127.0.0.1:3000/s/{token}",
  "expires_at": "2026-10-27T02:34:01.879291+00:00",
  "include_raw": false,
  "ttl_days": 7 }
```

- `report_id` → `404 REPORT_NOT_FOUND`（与其它读接口一致，不区分"不存在"与"不是你的"）。
- `share_url` 是**分享令牌**而不是 `report_id`：`{token}` 是 32 字符不可猜测串，库里只存它的 sha256；
  默认 7 天有效、可撤销（`DELETE /reports/{report_id}/share` → `204`）。
- `include_raw=false` 时公开页（`GET /s/{token}` 对应前端路由 `/s/[token]`）**不返回**简历原文与改写句；
  联系方式（手机号 / 邮箱）在任何情况下都会打码。
- `POST` 保留为兼容形式（老前端在用）；新客户端按 `GET` 调用即可。

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

> **鉴权**：本节 4 个端点都要求 `users.role == "admin"`（`api/deps.py::require_admin`），
> 否则返回 `403 {"detail":{"code":"ADMIN_REQUIRED","message":"需要管理员权限"}}`。
> 匿名设备通道（`X-Device-Id`）同样会被拒绝。前端 `/admin` 只做体验层的角色判断，
> 真正的权限边界在后端；管理员可由 `python -m scripts.grant_admin --email <邮箱>` 授予。

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

#### 6.5.1 批次进度流（SSE，推荐；详情接口仍然保留）

`GET /api/v1/hr/batches/{batch_id}/stream` → `text/event-stream`

**为什么加**：批量 100 份、每份约 30s，前端 2s 轮询只能看到 `3/100` 这种数字跳变，
HR 在整个过程中几乎看不到进展。SSE 让**每个候选人一出结果就推一行**，
HR 可以在第 5 个人出结果时就开始筛，不必等整批跑完。

| 项 | 约定 |
|---|---|
| 认证 | 同其它接口（`X-Device-Id` 请求头） |
| 客户端 | ⚠️ **必须用 `fetch()` + `ReadableStream`，不能用 `EventSource`**（无法自定义请求头） |
| 归属校验 | 非本人批次返回 `BATCH_NOT_FOUND`（不区分"不存在"与"不是你的"） |
| 服务端轮询 | 500ms 一次读 `hr_batch_items`（批次可能跑在 Celery worker 里，只能以 DB 为唯一真源） |
| 心跳 | 每约 10s 一行 `: ping` |
| 上限 | 3600s 后发 `error` 并关闭（批次可能跑几十分钟，比单份的 900s 宽） |
| 兼容 | §6.5 详情接口**不删除**，断线续取与老客户端继续可用 |

**事件模型**

| event | data | 何时发 |
|---|---|---|
| `item_done` | 单个候选人条目（与 §6.5 的 `items[]` 元素同构：`resume_id/resume_title/status/overall_score/match_score/risk_level/report_id/summary`） | 该候选人出结果（`done`/`failed`），每人只发一次（按 `report_id` 去重） |
| `progress` | `{batch_id, status, finished, total}` | `finished/total` 变化时 |
| `done` | 完整批次详情（**与 §6.5 详情接口同构**，`items` 已按总分降序） | 整批完成；随后服务端关闭 |
| `error` | `{message, batch_id?}` | 批次不存在 / 执行失败 / 超过 3600s |

**前端用法**：与 §4.4 完全一致（同样的跨 chunk 缓冲解析），只是把 `step_done` 换成 `item_done`、
进度字段换成 `finished/total`；失败同样**自动回落到 §6.5 的 2s 轮询**。

### 6.6 简历模板

`GET /api/v1/templates` → `{ "items": [TemplateSummary] }`

```json
{
  "code": "celadon_line",
  "name": "青简细线",
  "description": "细线单栏版式：技能前置、留白克制，适合社招研发 / 算法 / 测试 / 运维投递",
  "target": "experienced",
  "role_family": "tech",
  "section_count": 8,
  "style": "minimal",
  "layout": "single",
  "design": "celadon",
  "accent": "#5D7F6B",
  "tags": ["社招", "技术", "单栏"]
}
```

`target` ∈ `campus`（应届） | `experienced`（社招） | `transition`（转行）。
`style` ∈ `minimal` | `business` | `fresh` | `creative`；`layout` ∈ `single` | `sidebar`；
`design` 是前端版式组件标识（第一批：`celadon` / `navy` / `ivory` / `cobalt`）；
`tags` 是画廊筛选用的中文标签（社招 / 实习 / 校招 / 单栏 / 双栏等）。

**列表只返回 `visible=true` 的模板**（当前 4 套版式）。旧写作模板（`general_experienced` 等）
不再出现在列表里，但 `GET /templates/{code}` 仍可查到，保证已保存简历按旧 `template_code` 打开不 404。

`GET /api/v1/templates/{code}` → TemplateDetail

```json
{
  "code": "celadon_line",
  "name": "青简细线",
  "description": "…",
  "target": "experienced",
  "role_family": "tech",
  "section_count": 8,
  "style": "minimal",
  "layout": "single",
  "design": "celadon",
  "accent": "#5D7F6B",
  "tags": ["社招", "技术", "单栏"],
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
| `RESUME_UNANALYZABLE` | 400 | 简历没有可分析的文本或结构（扫描件 OCR 失败 / 空文件 / 多栏排版解析失败）。**此时不产出报告** —— 给一份"中等水平、风险较低"的假结论比报错更伤信任 |
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
| `ADMIN_REQUIRED` | 403 | 已登录但不是管理员（访问 `/api/v1/admin/*`） |
| `LLM_ERROR` | 502 | LLM 调用失败且降级不可用 |
| `INTERNAL_ERROR` | 500 | 未预期错误 |

---

## 9. 页面与接口对应关系（前端实现依据）

| 页面 | 路由 | 使用接口 |
|---|---|---|
| 首页 | `/` | 2.1 上传简历、3.1 创建 JD、4.1 创建任务、6.1 套餐 |
| 简历编辑开始页 | `/editor`（`?template=` 预选模板；`?resumeId=` 服务端重定向到编辑器） | 6.6 模板、2.2 我的简历列表、2.1 上传（导入现有简历） |
| 简历编辑器 | `/editor/write?start=blank\|draft\|resume&resumeId=&template=` | 6.6 模板、2.3 编辑器创建简历、2.3.1 更新、2.4 导出、3.1 创建 JD、4.1 创建任务、2.2 载入/删除已保存简历 |
| 进度页 | `/analyze/[taskId]` | 4.2 轮询 |
| 报告页 | `/report/[reportId]` | 5.1 获取报告、5.2 markdown 导出 |
| 账号中心 | `/user` | 1.x 账号接口、2.2 我的简历列表/删除 |
| 分享页 | `/s/[token]` | `GET /api/v1/shares/{token}`（只读，隐藏操作区；不传 `X-Device-Id`） |
| HR 批量 | `/hr` | 3.1、2.2、6.5 |
| 管理后台 | `/admin` | 6.3 |
| 定价 | `/pricing` | 6.1、6.1 下单 |

**闭环约定**：报告页的「修改后重新诊断」行为由报告的 `resume_source` 决定 ——
`editor` 时跳 `/editor/write?start=resume&resumeId={resume_id}` 并回填原内容；`upload` 时跳首页重新上传。
编辑器内的「发起诊断」走 §2.3 创建简历 → §3.1 创建 JD → §4.1 创建任务，成功后跳 `/analyze/{task_id}`。

**开始页的文档导入**复用 §2.1 的文件上传（不新增接口）：`/editor` 上传拿回 `parsed` 后，
用编辑器侧的映射函数填入表单并进入 `/editor/write`。注意此时应**丢弃该上传件的 `resume_id`** ——
上传件没有 `editor_content`，导出会退化为「以解析结果为源」的有损渲染；
置空后，用户下一次导出或发起诊断时会由 §2.3 另存为一份带完整结构化内容的可编辑版本。
