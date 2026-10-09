# ZhenResume 端到端工作流技术分析（请求生命周期 / 状态机）

> 分析对象：`E:\ZhenResume`（FastAPI + SQLAlchemy 后端，Next.js 前端）。
> 本文所有结论均以「读取到的代码 + 行号」为据；非代码可直接证明的推断一律显式标注【推断】/【实测】。
> 行号格式 `path:line` 均为分析当次读取的版本。

---

## 0. 证据范围与验证方法

### 0.1 已逐行读取的文件

| 层 | 文件 |
| --- | --- |
| 工作流核心 | `backend/app/workflow/engine.py`（235 行，全文）、`backend/app/workflow/__init__.py` |
| Agent 层 | `backend/app/agents/__init__.py`、`base.py`、`parse_agent.py`、`report_agent.py`、`qc_agent.py`、`suggestion_agent.py`、`finding_utils.py`（部分），以及 `ats/impact/risk/jd_match` 的关键行 |
| API 层 | `backend/app/api/tasks.py`、`resumes.py`、`reports.py`、`deps.py`、`health.py`、`jds.py`、`hr.py`、`__init__.py` |
| 应用/基础设施 | `backend/app/main.py`、`db.py`、`config.py`、`models.py`、`schemas.py`、`serializers.py`、`errors.py` |
| 服务层 | `services/llm.py`、`services/rag.py`、`services/prompts.py`、`services/embeddings.py`、`services/extract.py`（部分）、`services/analyze.py`（评分与排序段） |
| 前端 | `frontend/app/page.tsx`、`frontend/app/analyze/[taskId]/page.tsx`、`frontend/components/step-timeline.tsx`、`frontend/lib/api.ts`（任务相关段）、`frontend/lib/types.ts`（任务相关段） |
| 第三方（venv 实读） | `backend/.venv/Lib/site-packages/starlette/background.py`、`starlette/responses.py` |

### 0.2 两项实测（用项目自身的 `app.db` 与 `session_scope`，在 `%TEMP%` 下的一次性 SQLite 文件上复现，未改动任何项目文件）

1. **事务可见性**：在 `session_scope()` 打开的事务内 `flush()` 写入 `status/current_step/progress`，另一个 `SessionLocal()` 读到的仍是旧值；`commit()` 之后才读到新值。`journal_mode` 实测为 `wal`。
2. **并发写**：第一个 `session_scope` 事务未结束（持写锁）时，第二个会话写入 `analysis_tasks` 在约 5.5s 后失败：`sqlite3.OperationalError: database is locked`。
3. **端到端**（mock 模式，真实跑 `run_workflow`）：轮询端在整条工作流执行期间只观测到 `('pending', None, 0.0)`，工作流返回后观测到 `('done', 'qc', 1.0)`，**没有任何中间态**（`running` / `current_step=parse…` / 0.125 等均不可见）。

> 这三点直接决定了本文第 8 章的风险结论，其中第 3 点与产品文案（`frontend/app/analyze/[taskId]/page.tsx:118`、`frontend/app/page.tsx:279` 宣称可观察「每个 Agent 的实时状态」）冲突。

---

## 1. 端到端流程（按跳序，每跳给出函数与位置）

### ① 上传简历：浏览器 → `POST /api/v1/resumes` → `Resume` 行

- 前端：`HomePage.handleSubmit`（`frontend/app/page.tsx:71-105`）先 `setPhase('uploading')`，调用 `uploadResume(file, file.name)`（`frontend/app/page.tsx:87`）。
- HTTP 封装：`uploadResume`（`frontend/lib/api.ts:270-275`）用 `FormData` 提交；请求统一走 `request()`（`frontend/lib/api.ts:187-232`），携带 `X-Device-Id`（`frontend/lib/api.ts:192`，值来自 `localStorage`，见 `167-185`）。
- 路由表：`api_router` 前缀 `/api/v1`（`backend/app/api/__init__.py:7`），`resumes.router` 前缀 `/resumes`（`backend/app/api/resumes.py:25`）。
- 处理函数：`create_resume`（`backend/app/api/resumes.py:68-113`）：校验扩展名（`76-77`）→ 读取字节与大小/空文件校验（`79-84`）→ 落盘 `settings.upload_dir`（`87-90`）→ **同步抽文**（`92`）→ **同步规则解析**（`93-94`）→ 建 `Resume` 行并 `commit`（`96-108`）。

```python
# backend/app/api/resumes.py:92-94
    result = extract_service.extract_text(data, filename)
    raw_text = result.text
    parsed = parse_resume_text(raw_text) if raw_text else ParsedResume.empty()
```

关键点：`raw_text` 与 `parsed_json` 在此刻就已落库（`resumes.py:100-101`），**工作流的 RAG 检索与 AgentContext 初值用的是这份「上传时解析结果」**（见第 5 章）。抽文为空是已预期的分支（`services/extract.py:45-47` 对扫描件的说明），此处**不拦截**空文本。

### ② 创建 JD：浏览器 → `POST /api/v1/jds` → `JobDescription` 行

- 前端：`createJd`（`frontend/app/page.tsx:90-94`）→ `frontend/lib/api.ts:296-298`。
- 后端：`create_jd`（`backend/app/api/jds.py:20-37`），`parse_jd_text(payload.raw_text)`（`jds.py:26`）后 `commit`（`jds.py:35`）。
- 入参下限由 `JdCreate.raw_text` 的 `min_length=20` 与 `field_validator` 双重保证（`backend/app/schemas.py:25-33`）。

### ③ 创建任务：浏览器 → `POST /api/v1/tasks` → `AnalysisTask` 行（`pending`）→ 挂后台任务

- 前端：`createTask({resume_id, jd_id})`（`frontend/app/page.tsx:97`，`frontend/lib/api.ts:316-318`），成功后 `router.push('/analyze/' + task.task_id)`（`frontend/app/page.tsx:100`）。
- 后端：`create_task`（`backend/app/api/tasks.py:29-63`），`status_code=202`：

```python
# backend/app/api/tasks.py:36-41,44-45,47-57,61
    resume = db.get(Resume, payload.resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()
    jd = db.get(JobDescription, payload.jd_id)
    if jd is None or jd.user_id != user.id:
        raise JdNotFound()
    ...
    if user.free_quota_used >= settings.free_quota and not _has_paid_access(db, user):
        raise QuotaExceeded(f"免费额度 {settings.free_quota} 次已用完，请选择付费套餐继续诊断")

    task = AnalysisTask(user_id=user.id, resume_id=resume.id, jd_id=jd.id,
                        type="single", status="pending", progress=0.0)
    db.add(task)
    user.free_quota_used = (user.free_quota_used or 0) + 1
    db.commit()
    db.refresh(task)

    # 异步执行：Starlette 会 await 这个 async 函数，请求立即返回 202
    background_tasks.add_task(launch_workflow, task.id)
```

- 身份：`get_current_user`（`backend/app/api/deps.py:18-29`）用 `X-Device-Id`（缺省 `"anonymous"`，`deps.py:22`）查/建 `User`，无登录、无 JWT。
- 配额闸门：`settings.free_quota = 3`（`backend/app/config.py:56`），付费判定只数 `Payment.status == "paid"`（`backend/app/api/tasks.py:21-26`）。
- 返回值仅 `{"task_id", "status"}`（`tasks.py:63`），前端据此跳转。

### ④ 后台启动：`launch_workflow` → `asyncio.run(run_workflow)`

- `backend/app/workflow/engine.py:38-43`（同步函数）：`asyncio.run(run_workflow(task_id))`，异常 `traceback.print_exc()` 吞掉。
- 详见第 2 章。

### ⑤ `run_workflow` 前段：读上下文、写 `running`、一次性 RAG

```python
# backend/app/workflow/engine.py:50-77（节选）
    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return None
        resume = db.get(Resume, task.resume_id) if task.resume_id else None
        jd = db.get(JobDescription, task.jd_id) if task.jd_id else None
        if resume is None or jd is None:
            _fail(db, task, "简历或 JD 记录缺失")
            return None

        task.status = "running"
        task.progress = 0.0
        task.error = None
        db.flush()
```

随后构造 `AgentContext`（`engine.py:65-72`），其中 `resume_text=resume.raw_text or ""`、`jd_text=jd.raw_text or ""`，`parsed_*` 经 `_safe_resume`/`_safe_jd` 容错（`engine.py:145-156`，校验失败则回落到 `ParsedResume.empty()/ParsedJd.empty()`），`prompt_overrides=prompts.load_overrides(db)`（`engine.py:71` → `services/prompts.py:86-93`，取 `prompt_templates` 每 name 的最新版本）。

### ⑥ 8 个 Agent 串行循环（一个 `WorkflowStep` 一行，逐步更新）

顺序由 `AGENT_PIPELINE` 固定（`backend/app/agents/__init__.py:13-22`）：`parse → jd_match → ats → impact → risk → suggestion → report → qc`；节点数 `TOTAL_STEPS = len(AGENT_PIPELINE)`（`engine.py:35`）= 8。循环体见第 3 章。

### ⑦ 报告持久化：在 `qc` 步骤之后触发

```python
# backend/app/workflow/engine.py:116-120
            if agent.name == "qc":
                # QC 会改动 findings，必须在它之后再定稿分数，保证总分与问题清单一致
                ctx.scores = analyze.compute_scores(ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
                report = _persist_report(db, task, ctx)
                report_id = report.id
```

`_persist_report`（`engine.py:159-201`）写 1 行 `AnalysisReport` + N 行 `ReportFinding`（`185-199`，按 `analyze.sort_findings` 排序写 `sort_order`）。

### ⑧ 终态写回：任务置 `done`

```python
# backend/app/workflow/engine.py:122-132
        if report_id is None:
            _fail(db, task, "工作流未产出报告")
            return None

        task.status = "done"
        task.progress = 1.0
        task.current_step = "qc"
        task.completed_at = datetime.now(timezone.utc)
        db.flush()

        return report_id
```

### ⑨ 前端轮询：`GET /api/v1/tasks/{task_id}`

- 路由：`get_task`（`backend/app/api/tasks.py:85-96`）：归属校验（`92-93`，非本人 → `TaskNotFound`）→ 查报告 id（`95`）→ `serialize_task(task, steps=list(task.steps), report_id=report_id)`（`96`）。
- 序列化：`serialize_task`（`backend/app/serializers.py:115-129`）按固定 `STEP_ORDER`（`serializers.py:112`）补齐缺失步骤为 `pending`（`117-118`），返回 `status/progress/current_step/total_steps(=8)/steps/report_id/error`。
- 前端轮询与渲染：见第 6 章。

### ⑩ 跳转报告页

`data.status === 'done' && data.report_id` → `router.replace('/report/' + data.report_id)`（`frontend/app/analyze/[taskId]/page.tsx:68-73`）。报告页路由存在（`frontend/app/report/[reportId]/page.tsx`），数据来自 `GET /api/v1/reports/{report_id}`（`backend/app/api/reports.py:35-42`，`_load` 在 `19-32`）。**本文未审阅报告页内部实现**，此处仅确认路由与接口存在。

---

## 2. 后台工作如何启动：BackgroundTasks + `asyncio.run`

### 2.1 调用链（含第三方实读证据）

1. `create_task` 返回前把同步函数注册进 `BackgroundTasks`：`background_tasks.add_task(launch_workflow, task.id)`（`backend/app/api/tasks.py:61`）。
2. `launch_workflow` 是**同步** `def`（`backend/app/workflow/engine.py:38`）。
3. Starlette 对同步可调用对象不 `await`，而是丢进线程池：

```python
# backend/.venv/Lib/site-packages/starlette/background.py:22,24-28
        self.is_async = is_async_callable(func)

    async def __call__(self) -> None:
        if self.is_async:
            await self.func(*self.args, **self.kwargs)
        else:
            await run_in_threadpool(self.func, *self.args, **self.kwargs)
```

4. 响应体先发出，后台任务后执行：

```python
# backend/.venv/Lib/site-packages/starlette/responses.py:155-158
        await send({"type": prefix + "http.response.body", "body": self.body})

        if self.background is not None:
            await self.background()
```

结论：客户端先拿到 202，随后 `launch_workflow` 在 anyio 线程池线程里跑。

### 2.2 为什么必须有 `asyncio.run`

```python
# backend/app/workflow/engine.py:38-43
def launch_workflow(task_id: str) -> None:
    """后台任务入口（供 FastAPI BackgroundTasks 调用）。异常全部吞掉并落到任务状态里。"""
    try:
        asyncio.run(run_workflow(task_id))
    except Exception:  # noqa: BLE001 —— 后台任务不能再往外抛，否则会静默丢失
        traceback.print_exc()
```

- `run_workflow` 是 `async def`（`engine.py:46`），整条链是异步的：`agent.safe_run`（`agents/base.py:93`）→ `llm.complete_json`（`services/llm.py:78`）→ `httpx.AsyncClient`（`services/llm.py:153`）。
- `launch_workflow` 跑在**线程池线程**里（上一节证据），该线程没有运行中的事件循环，因此必须自己建一个：`asyncio.run` 正是「在当前线程创建新事件循环、跑完协程、关闭循环」。
- 若改成在端点里直接 `await run_workflow(...)`，则 202 要等到整条流水线（含最多 3 次 × 60s 超时的 LLM 调用）结束才返回，与 `status_code=202`（`api/tasks.py:29`）和「请求立即返回」的契约冲突。
- 注意：`asyncio.run` 不能在已有运行中事件循环的线程里调用（会 `RuntimeError`）。所以 `launch_workflow` 必须保持**同步**；若有人把它改成 `async def`，这段代码会直接崩。同样地，HR 批量在自己的事件循环里**直接 `await run_workflow`**，没有二次 `asyncio.run`（`backend/app/api/hr.py:35-39` 与 `42,72`）。
- 离线脚本也是第二入口：`backend/scripts/inspect.py:58-63`、`backend/scripts/demo_improve_loop.py:155-160` 都是 `asyncio.run(run_workflow(task_id))`。

### 2.3 与注释不一致之处（精确说明）

`backend/app/api/tasks.py:60` 的注释写：

```
    # 异步执行：Starlette 会 await 这个 async 函数，请求立即返回 202
```

但 `launch_workflow` 是同步函数（`engine.py:38`），Starlette 走的是 `run_in_threadpool` 分支（`starlette/background.py:28`），不是 `await`。注释描述的执行模型与代码不符（「请求立即返回 202」这一结论本身仍然成立，但成立的原因是「响应先发送、后台任务后执行」，见 `starlette/responses.py:155-158`）。

---

## 3. 步骤循环：`WorkflowStep` 的创建/更新、进度、状态、失败与计量

### 3.1 建行（进入某步之前）

```python
# backend/app/workflow/engine.py:81-91
        for index, agent_cls in enumerate(AGENT_PIPELINE, start=1):
            agent = agent_cls()
            step = WorkflowStep(
                task_id=task_id,
                step_name=agent.name,
                status="running",
                input_json=_input_digest(agent.name, ctx),
            )
            db.add(step)
            task.current_step = agent.name
            db.flush()
```

- **没有 `pending` 阶段**：行一建立就是 `running`（`engine.py:83-88`）。前端看到的「等待中」步骤，全部来自序列化层的补齐（`serializers.py:117-118`）或前端补齐（`step-timeline.tsx:87-96`），而不是数据库真值。
- `input_json` 在 Agent 运行**之前**计算：`_input_digest`（`engine.py:204-218`）只记计数（`resume_chars/jd_chars/work_count/project_count/bullet_count/skill_count/jd_skill_count/knowledge_hits/findings_so_far/role_family`），因此 `findings_so_far` 是**该步开始前**的发现项数量。
- `WorkflowStep` 只有 `created_at`（`models.py:197`），无 `updated_at/completed_at`：整行是 INSERT 后原地 UPDATE。`AnalysisTask.steps` 关系按 `created_at` 排序（`models.py:126-128`），但 API 层最终按 `STEP_ORDER` 重排（`serializers.py:117-118`），排序不影响契约。

### 3.2 状态分支与异常

```python
# backend/app/workflow/engine.py:93-111
            started = time.perf_counter()
            try:
                output = await agent.safe_run(ctx, llm)
                if output.status == "degraded":
                    step.status = "degraded"
                else:
                    step.status = "done"
                step.notes = output.notes
            except Exception as exc:  # noqa: BLE001 —— critical Agent 失败即终止整个任务
                step.status = "failed"
                step.latency_ms = int((time.perf_counter() - started) * 1000)
                step.notes = f"{type(exc).__name__}: {str(exc)[:300]}"
                _fail(db, task, f"{agent.title} 执行失败：{str(exc)[:200]}")
                return None

            step.latency_ms = output.latency_ms or int((time.perf_counter() - started) * 1000)
            step.tokens = output.tokens
            step.cost = output.cost
            step.output_json = _truncate(output.data)
```

- `status` 取值：`running`（建行）/`done`/`degraded`/`failed`，与 `models.py:188-189` 注释一致。
- `degraded` 有两个来源：
  - 非 critical Agent 抛异常 → `safe_run` 兜底返回 `AgentOutput(data={}, status="degraded", notes=...)`（`agents/base.py:97-104`）；
  - LLM live 调用重试耗尽后回落 mock → `result.degraded=True` → Agent 返回 `status="degraded"`（例如 `report_agent.py:70`、`qc_agent.py:125`）。
  - **只有 `parse` 是 critical**（`parse_agent.py:59`），其异常会穿透 `safe_run`（`base.py:98-99`）被 `engine.py:101` 捕获。
- 失败即终止：`_fail` 之后 `return None`，循环不再继续，后续步骤不建行。
- Agent 契约见 `agents/base.py:85-104`；`AgentOutput` 计量属性的定义：

```python
# backend/app/agents/base.py:70-82
    @property
    def tokens(self) -> int:
        if not self.llm:
            return 0
        return int(self.llm.tokens_in + self.llm.tokens_out)

    @property
    def cost(self) -> float:
        return float(self.llm.cost) if self.llm else 0.0

    @property
    def latency_ms(self) -> int:
        return int(self.llm.latency_ms) if self.llm else 0
```

### 3.3 计量字段何时写入

| 字段 | 成功/degraded 路径 | 失败路径 |
| --- | --- | --- |
| `latency_ms` | `engine.py:108`：优先用 `output.latency_ms`（= LLM 自测时延），为 0 时回落到 `time.perf_counter()` 墙钟 | `engine.py:103`：墙钟 |
| `tokens` | `engine.py:109`：`tokens_in + tokens_out`（`base.py:71-74`） | 不写，保留列默认 `0`（`models.py:192`） |
| `cost` | `engine.py:110`：`llm.cost`（`base.py:77-78`；单价见 `config.py:40-41`，`services/cost.py` 计算） | 不写，保留默认 `0`（`models.py:193`） |
| `output_json` | `engine.py:111` + `_truncate`（`engine.py:221-235`：字符串 >4000 截断、列表 >30 项、字典 >40 键） | 不写（保持 `NULL`） |
| `notes` | `engine.py:100`（Agent 自述，如降级原因、QC 调整条数） | `engine.py:104`（异常摘要，≤300 字符 + 类型名） |

Mock 模式下的实际表现（可预测）：`llm.complete_json` 直接调用 `mock_factory` 并把其耗时作为 `latency_ms`（`services/llm.py:90-104`），`tokens_in/out = 0`、`cost = 0`（`llm.py:97-99`），因此**时间线上的 token 恒为 0、耗时是毫秒级规则引擎耗时**，不是真实模型耗时。

### 3.4 进度与 `current_step`

- `task.current_step = agent.name` 在该步**开始执行前**写入（`engine.py:90`）：语义是「正在跑的步骤」。
- `task.progress = round(index / TOTAL_STEPS, 4)` 在该步**成功之后**写入（`engine.py:113`，`index` 从 1 开始，`TOTAL_STEPS=8`）：依次为 `0.125 / 0.25 / 0.375 / 0.5 / 0.625 / 0.75 / 0.875 / 1.0`。
- 因此：第 1 步执行期间 `progress` 仍为 0.0；失败步骤不贡献进度（异常路径在 `engine.py:106` 提前 return，未执行 `113`），失败任务的 `progress` 停留在最后一个完成步骤的值。
- 收尾时 `current_step` 被显式写成常量 `"qc"`（`engine.py:128`），与循环最后一次赋值重复。
- 前端 `total_steps` 来自序列化常量 `len(STEP_ORDER)`（`serializers.py:112,125`）= 8，**不是** `TOTAL_STEPS`（`engine.py:35`）：两者是同一事实的两处独立硬编码（第 8.11 节列为维护风险）。

### 3.5 失败记录：`_fail`

```python
# backend/app/workflow/engine.py:138-142
def _fail(db: Session, task: AnalysisTask, message: str) -> None:
    task.status = "failed"
    task.error = message[:500]
    task.completed_at = datetime.now(timezone.utc)
    db.flush()
```

调用点与文案：

| 触发位置 | 条件 | `error` 文案 |
| --- | --- | --- |
| `engine.py:57` | 简历或 JD 行缺失（`task.resume_id/jd_id` 为 NULL 或已被删除） | `简历或 JD 记录缺失` |
| `engine.py:105` | Agent 抛异常（只有 critical `parse` 会走到这里） | `{agent.title} 执行失败：{异常摘要≤200}` |
| `engine.py:123` | 循环跑完但没有产出报告（即 `qc` 步骤未命中 `engine.py:116`） | `工作流未产出报告` |

`_fail` **不重置** `progress` 与 `current_step`，也不写 `steps` 里不存在的行；前端会把未执行的步骤补成 `pending` 展示，于是失败任务的时间线呈现「1 个 failed + 其余等待中」的混合态。

### 3.6 八个 Agent 对共享上下文（`AgentContext`）的写入

`AgentContext` 定义见 `agents/base.py:21-60`（累积字段：`findings/missing_keywords/covered_keywords/action_items/knowledge_refs/scores/summary/qc_report/outputs`）。

| # | `step_name` / `title` | 类与位置 | 对 `ctx` 的写 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | `parse` / 解析 Agent | `parse_agent.py:56-59` | `parsed_resume`(81)、必要时 `parsed_jd`(84-85)、`role_family`(87)、`outputs['parse']`(98) | **critical=True**；规则基线 + LLM 逐字段兜底合并（`_merge_parsed`，`16-53`） |
| 2 | `jd_match` / JD 匹配 Agent | `jd_match_agent.py:17-18` | 过滤 `findings`(74)、`missing_keywords`(101)、`covered_keywords`(102)、`add_findings`(100)、`outputs`(114) | 注入 `ctx.knowledge_digest()`（`:56`） |
| 3 | `ats` / ATS Agent | `ats_agent.py:16-17` | `add_findings`(54)、`outputs`(62) | 未注入知识片段 |
| 4 | `impact` / 影响力 Agent | `impact_agent.py:16-17` | `add_findings`(54)、`outputs`(65) | 注入知识片段（`:43`） |
| 5 | `risk` / 风险 Agent | `risk_agent.py:19-20` | `add_findings`(62)、`outputs`(72) | 未注入知识片段 |
| 6 | `suggestion` / 建议 Agent | `suggestion_agent.py:21-22` | 覆盖 `findings`(67)、`action_items`(68)、`outputs`(75) | 含 `_select_final`（每类保底一条，`:98-129`）与 `_ensure_minimum`（不足 5 条补齐，`:187-216`） |
| 7 | `report` / 报告合成 Agent | `report_agent.py:17-19` | `scores`(26)、`knowledge_refs`(27-36)、`summary`(58)、`outputs`(68) | 分数由规则层算（`:22`），LLM 只写 summary |
| 8 | `qc` / 质检 Agent | `qc_agent.py:32-34` | 覆盖 `findings`(102)、`qc_report`(103-110)、`outputs`(118) | 规则质检 + LLM 复核；`_ensure_minimum` 再补足（`:95`）；`sort_findings`（`:96`） |

发现项结构统一由 `finding_utils.coerce_findings` 白名单化（`finding_utils.py:43-`，`VALID_SEVERITY/VALID_CATEGORY` 见 `:16-17`），证据由后端重新定位（`resolve_evidence`，`:25-35`），不信模型给的 offset。

---

## 4. engine.py 里两个文档化的设计决策

`engine.py:12-14` 的模块 docstring 宣告了这两个决策：

```python
# backend/app/workflow/engine.py:12-14
两个关键设计决策：
1. **QC 之后重算分数**：质检会剔除/降级发现项，若不重算，报告总分与展示的问题清单会自相矛盾。
2. **input 只存摘要**：不把简历全文写进 workflow_steps，避免数据库膨胀与隐私外泄。
```

### 4.1 决策一：QC 之后重算分数

机制（逐步）：

1. 第 7 步 `report` 已经算过一次分数：`report_agent.py:22` 调 `analyze.compute_scores(...)`，写入 `ctx.scores`（`:26`），并把 `scores` 放进本步 `output`（`:60-68`）——**这次分数只用于写 summary 与该步的 output_json**。
2. 第 8 步 `qc` 会**改动 `ctx.findings`**：规则质检 drop/downgrade（`qc_agent.py:43-54`）、LLM 复核再 drop/downgrade（`71-93`）、`_ensure_minimum` 重新补足到 ≥5 条（`:95`）、`sort_findings` 重排（`:96`），最终 `ctx.findings = kept`（`:102`）。
3. 引擎以 `agent.name == "qc"` 为触发点，在 QC 之后重算并落库（`engine.py:116-120`，见 1-⑦ 引用）：`ctx.scores` 被覆盖为**基于 QC 后 findings** 的分数，随后 `_persist_report` 使用它。
4. `_persist_report` 里所有分数与等级都取自这次重算结果：`overall/ats/impact/clarity/match/risk`（`engine.py:163-168`），`risk_level=analyze.risk_level_from_score(int(scores.get("risk") or 0))`（`engine.py:169`；分级阈值见 `analyze.py:686-692`：≥80 green、60–79 yellow、<60 red），findings 同样按最终列表排序落库（`engine.py:185`）。

后果与副作用：

- 报告顶层 `scores` 与 `findings` 一致（`serializers.py:178-188` 直接读列与关联行）——设计目标达成。
- **但** `extra_json.agent_outputs` 里保留了 `report` 步骤**QC 之前**的 `scores`（`engine.py:179` 写 `ctx.outputs`；`report_agent.py:60-68` 的 output 含 `"scores": scores`）。即数据库里同一个报告存在两套分数：`report.overall_score`（QC 后）与 `extra_json.agent_outputs.report.scores`（QC 前）。该字段未出现在 `serialize_report` 的输出里（`serializers.py:169-193` 未暴露 `agent_outputs`），因此**当前不影响前端**，属于库内一致性问题。
- QC 步骤自身的 `output_json` 里的 `severity_breakdown` 是 QC 后的分布（`qc_agent.py:114-117`），与落库分数同源，是一致的。
- 由于 QC 会调用 `_ensure_minimum`（`qc_agent.py:95`），「被 drop 的条目」可能被通用检查项替换后再参与评分，即分数是**最终清单**的函数，而不是「原清单减去被删项」的函数。

### 4.2 决策二：`input_json` 只存摘要

机制：

- 建行时调用 `_input_digest(agent.name, ctx)`（`engine.py:87`），实现只输出计数：

```python
# backend/app/workflow/engine.py:204-218（节选）
def _input_digest(agent_name: str, ctx: AgentContext) -> dict:
    """只存摘要：既便于排查，又不会把简历全文写进库。"""
    return {
        "agent": agent_name,
        "resume_chars": len(ctx.resume_text),
        "jd_chars": len(ctx.jd_text),
        ...
        "knowledge_hits": len(ctx.knowledge_hits),
        "findings_so_far": len(ctx.findings),
        "role_family": ctx.role_family,
    }
```

- 与之对称的是输出侧的 `_truncate`（`engine.py:221-235`），只对 `workflow_steps.output_json` 生效。

需要如实指出的边界（与「隐私不外泄」目标相关的部分）：

1. 简历全文确实仍以明文存在 `resumes.raw_text`（`models.py:78`，写入点 `resumes.py:100`），`workflow_steps` 只是不重复存一份。
2. **报告行里也没有做同类脱敏**：`_persist_report` 把 `ctx.outputs` 原样（未经 `_truncate`）写进 `analysis_reports.extra_json.agent_outputs`（`engine.py:172-180`），而 `ctx.outputs["parse"]` 包含 `parsed_resume.model_dump()`（`parse_agent.py:90-97`），即整份简历的结构化内容会进入报告行。所以「不把简历写进工作流表」成立，「简历内容不落其他表」不成立。
3. `workflow_steps` 中仍可能间接包含简历片段：`output_json` 里的 findings 含 `original_text`（`_persist_report` 用的同一批数据，见 `engine.py:186-199`），只是被 `_truncate` 限制在 4000 字符/字段。

---

## 5. RAG 检索在流水线中的位置：一次检索、全链路复用

```python
# backend/app/workflow/engine.py:74-77
        # RAG 检索：一次检索，全链路复用，避免每个 Agent 重复打库
        query = rag.build_query(ctx.parsed_jd.model_dump(), ctx.parsed_resume.model_dump())
        if query.strip():
            ctx.knowledge_hits = rag.search(db, query, top_k=5)
```

- 位置：在**进入 8 步循环之前**（`engine.py:81` 之前），早于 `parse` Agent。
- 输入是 **DB 里上传时解析出的** `parsed_json`（`engine.py:69-70` ← `resumes.py:101` / `jds.py:32`），**不是** `parse` Agent 用 LLM 纠正后的结果。因此检索质量不受第 1 步改进的影响（若上传时解析很差，检索 query 也差）。【代码可证的事实陈述】
- query 构造：`rag.build_query`（`services/rag.py:102-115`）拼 JD `title/seniority` + `skills_core/keywords/responsibilities` 各前 6 项 + 简历 skills 前 8 项，整体截断到 600 字符。
- 检索实现：`rag.search`（`rag.py:60-99`）全表扫描 `knowledge_chunks`（`:73`）→ 本地哈希向量（`services/embeddings.py:58-64`，无外部 embedding API）+ 词面重合度混合打分 `0.6*cos + 0.4*lex`（`rag.py:83`）→ 过滤 `min_score=0.05` → 排序取 `top_k`（`:98-99`）。
- 复用方式（同一份 `ctx.knowledge_hits`）：
  - 提示词片段：`ctx.knowledge_digest()`（`agents/base.py:53-60`，取前 3 条、每条 ≤300 字符）被 `jd_match_agent.py:56`、`impact_agent.py:43`、`suggestion_agent.py:37` 注入。
  - 引用落库：`report_agent.py:27-36` 取 `ctx.knowledge_hits[:3]` 生成 `knowledge_refs` → `report.extra_json.knowledge_refs`（`engine.py:176`）→ `serialize_report` 的 `knowledge_refs`（`serializers.py:191`）。
  - 证据判定：`finding_utils.resolve_evidence`（`:32-34`）在简历/JD 都定位不到引文时，会尝试命中知识片段并标记 `source="knowledge"`；QC 把「source=knowledge」视为可定位证据，从而允许红色风险保留（`qc_agent.py:142-154`）。
  - 未消费知识片段的步骤：`parse`、`ats`、`risk`（这三个文件中没有 `knowledge_digest` 调用）。
- `top_k=5` 是**硬编码字面量**（`engine.py:77`），而配置里有 `settings.rag_top_k = 5`（`config.py:45`）未被此处使用；`rag.search` 自身默认 `top_k=5`（`rag.py:64`）。

---

## 6. 前端并发/轮询契约

### 6.1 轮询参数与驱动方式（`frontend/app/analyze/[taskId]/page.tsx`）

```tsx
// frontend/app/analyze/[taskId]/page.tsx:16
const POLL_INTERVAL_MS = 1500;
```

```tsx
// frontend/app/analyze/[taskId]/page.tsx:60-89（节选）
    const poll = async () => {
      try {
        const data = await getTask(taskId);
        if (cancelled) return;
        setTask(data);
        setLoading(false);
        setError(null);

        if (data.status === 'done' && data.report_id) {
          if (!redirectedRef.current) {
            redirectedRef.current = true;
            router.replace(`/report/${data.report_id}`);
          }
          return;
        }
        if (data.status === 'failed') {
          return;
        }
      } catch (err) { ... }

      if (!cancelled) {
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    void poll();
```

契约要点：

| 项 | 实现 | 位置 |
| --- | --- | --- |
| 间隔 | 1500 ms；**先立即请求一次**，之后用 `setTimeout` 递归（非 `setInterval`，避免请求堆叠） | `page.tsx:16,85,89` |
| 终态 | `done`（且 `report_id` 非空）→ `router.replace` 并 `return`，不再排下一次；`failed` → `return`，不再排下一次 | `page.tsx:68-77` |
| 重定向防抖 | `redirectedRef`（`useRef`）保证只跳一次 | `page.tsx:41,69-72` |
| 卸载清理 | `cancelled` 标志 + `clearTimeout` | `page.tsx:50-58,91-94` |
| 请求取消 | **未使用** `AbortSignal`：`getTask(taskId)` 未传 signal（`page.tsx:62`），而 `getTask` 本身支持（`lib/api.ts:321-322`） | 同上 |
| 错误处理 | 任何异常（含 404 `TASK_NOT_FOUND`）只设置错误文案，**仍会继续排下一次轮询** | `page.tsx:78-86` |
| 传输 | `fetch(..., { cache: 'no-store' })`，每次请求带 `X-Device-Id` | `lib/api.ts:204-210,192` |

`lib/api.ts:320` 的注释「轮询任务状态（契约建议 1.5s 一次）」与 `POLL_INTERVAL_MS` 一致。

### 6.2 进度条与时间线的数据来源

```tsx
// frontend/app/analyze/[taskId]/page.tsx:97-100
  const status = task?.status ?? 'pending';
  const running = status === 'pending' || status === 'running';
  const failed = status === 'failed';
  const progressPercent = Math.round(Math.min(1, Math.max(0, task?.progress ?? 0)) * 100);
```

- 进度条来源：后端 `AnalysisTask.progress`（`models.py:119`，`Numeric(5,4)`；由 `engine.py:113` 写入）→ `serialize_task` 的 `progress: float(task.progress or 0)`（`serializers.py:123`）→ 前端夹到 `[0,1]` 再 ×100（`page.tsx:100`）→ `<Progress value={progressPercent} />`（`page.tsx:166`）。**不是**前端按步骤数自行推算。
- 文案：`当前步骤：{task?.current_step ?? '等待调度'} · 共 {task?.total_steps ?? 8} 步`（`page.tsx:161-163`）；`total_steps` 由后端给出 8（`serializers.py:125`）。
- 时间线：`<StepTimeline steps={task?.steps ?? []} />`（`page.tsx:172`）。组件内按固定顺序渲染：

```tsx
// frontend/components/step-timeline.tsx:6-15,87-96（节选）
export const STEP_ORDER: StepName[] = ['parse','jd_match','ats','impact','risk','suggestion','report','qc'];
...
  const ordered: TaskStep[] = STEP_ORDER.map(
    (name) =>
      byName.get(name) ?? { step_name: name, status: 'pending' as StepStatus, latency_ms: null, tokens: null, cost: null },
  );
```

- 每行展示「耗时 / token」（`step-timeline.tsx:138-141`），数据来自 `serialize_step`（`serializers.py:102-109`）。后端已保证 8 行齐全（`serializers.py:117-118`），前端补齐是双保险。
- 任务级状态文案表 `STATUS_TEXT`（`page.tsx:18-23`）只有 `pending/running/done/failed`；步骤级文案表（`step-timeline.tsx:68-73`）同样只有 `pending/running/done/failed` —— **都不含 `degraded`**（见 8.4）。

### 6.3 首页（触发侧）的并发保护

- `Phase` 状态机 `'idle' | 'uploading' | 'parsing_jd' | 'creating_task' | 'redirecting'`（`frontend/app/page.tsx:15`），`busy = phase !== 'idle'`（`:68`），`handleSubmit` 开头 `if (busy) return;`（`:73`），提交按钮 `disabled={busy || !file || ...}`（`:263`）。这是**纯客户端**的重复提交防护；服务端没有幂等键（见 8.5）。
- 三步串行：上传 → 建 JD → 建任务（`page.tsx:87-97`），任一步失败则 `setError` 并回 `idle`（`:101-104`）。**前端不检查 `resume.raw_text` 是否为空**（仅用 `resume.resume_id`，`:97`），扫描件抽文为空也会照常发起诊断（见 8.6）。

---

## 7. 状态机总表

### 7.1 任务级（`analysis_tasks.status`）

| 当前态 | 事件 | 次态 | 设置者（函数:行） | 附加写入 |
| --- | --- | --- | --- | --- |
| （不存在） | 创建任务 | `pending` | `api/tasks.py:47-55` `create_task` | `progress=0.0`、`type="single"`、`created_at` |
| `pending` | 后台任务启动 | `running` | `workflow/engine.py:60-63` `run_workflow` | `progress=0.0`、`error=None` |
| `pending` | 简历/JD 行缺失 | `failed` | `engine.py:56-58` → `_fail:138-142` | `error="简历或 JD 记录缺失"`、`completed_at` |
| `running` | 某步抛异常 | `failed` | `engine.py:101-106` → `_fail` | `error="…执行失败：…"`、`completed_at`、该步 `status=failed` |
| `running` | 循环结束但无报告 | `failed` | `engine.py:122-124` → `_fail` | `error="工作流未产出报告"` |
| `running` | 8 步全部完成且报告已落库 | `done` | `engine.py:126-130` | `progress=1.0`、`current_step="qc"`、`completed_at` |
| `done` / `failed` | — | 终态，**无任何回退/重试路径** | — | — |

- 终态判定条件（前端/后端同源）：`done` 的完整条件 = `status=="done"` **且** `report_id != null`（`page.tsx:68`；后端 report_id 来自 `tasks.py:95` 的 `analysis_reports.task_id` 查询）。
- `pending` 也是「永久卡死」形态：如果异常逃出 `session_scope` 事务块（见 8.3），任务会永远停在 `pending`，无超时兜底、无 watchdog。

### 7.2 步骤级（`workflow_steps.status`）

| 当前态 | 事件 | 次态 | 设置者:行 | 说明 |
| --- | --- | --- | --- | --- |
| （不存在） | 进入该步 | `running` | `engine.py:83-89` | 建行即 `running`；同时写 `input_json` 摘要、`task.current_step` |
| `running` | Agent 正常返回 | `done` | `engine.py:96-100` | 写 `notes`（可能为 `None`） |
| `running` | Agent 返回 degraded（LLM 降级 / 非 critical 异常被 `safe_run` 兜底） | `degraded` | `engine.py:96-97`；来源 `agents/base.py:100-104`、`services/llm.py:116-131` | 流水线**继续**执行 |
| `running` | Agent 抛异常且 `critical=True`（当前仅 `parse`） | `failed` | `engine.py:101-104` | 同时 `_fail` 任务并 return，后续步骤不建行 |
| 任意非终态 | — | 无重试 | — | 引擎不重跑任何步骤 |
| 未建行 | — | 仅 API/前端展示为 `pending` | `serializers.py:117-118`、`step-timeline.tsx:87-96` | 数据库无此行 |

### 7.3 计量与产物（每一步成功时同步写入）

| 产物 | 位置 | 何时 |
| --- | --- | --- |
| `workflow_steps.input_json` | `engine.py:87,204-218` | 步骤开始前 |
| `workflow_steps.output_json` | `engine.py:111,221-235` | 步骤成功后（截断后） |
| `latency_ms / tokens / cost` | `engine.py:108-110` | 步骤成功后（重试/降级后取 mock 的值） |
| `task.progress` | `engine.py:113` | 步骤成功后 |
| `analysis_reports` + `report_findings` | `engine.py:116-120,159-201` | **仅** `qc` 步骤完成后 |
| `analysis_tasks.status/progress/completed_at` | `engine.py:126-130` | 循环结束 |

---

## 8. 边界情况与风险（按严重度排列）

标注约定：**【实测】**= 本次运行验证；**【代码可证】**= 仅靠读取代码即可确定；**【推断】**= 由代码结构推导，未端到端运行验证。

### 8.1 【实测】轮询接口在整个执行期只能看到 `pending`：产品承诺的「实时步骤状态」不成立

- 机制：`run_workflow` 把整条流水线包在**一个** `session_scope()` 里（`engine.py:50`），中间只用 `db.flush()`（`engine.py:63,91,114,130` 等），`db.commit()` 只在 `session_scope` 退出时执行一次（`db.py:62-72`）。另一个连接（`GET /tasks/{id}` 用的请求级 session，`db.py:52-58`）在提交前看不到这些写入。
- 实测（mock 模式真实调用 `run_workflow`，另一个线程以 4ms 间隔轮询）观察到的状态序列只有两项：

```
('pending', None, 0.0)
('FINAL', 'done', 'qc', 1.0)
```

- 结论：`running` / `current_step=parse…` / `progress=0.125…` / 单步 `done/degraded/failed` **在任务结束前对 API 完全不可见**；提交后第一次轮询同时拿到 `done` 与 `report_id`，页面随即跳转报告页（`page.tsx:68-73`）。因此 `page.tsx:118`「你可以留在本页观察每个 Agent 的实时状态」与 `frontend/app/page.tsx:279`「期间可在进度页看到每个 Agent 的实时状态」与现状不符（用户实际看到的是长时间 0% + 一次跳转）。
- 反过来说明：`serialize_task` 的补 `pending` 逻辑（`serializers.py:117-118`）在真实运行期渲染的就是「8 行全部等待中」。
- 修复方向（不在本次改动范围，仅记录）：按步 `commit`，或把进度写入独立会话/独立表，或改用轮询外的推送通道。

### 8.2 【实测】一个工作流持写锁贯穿全程：并发写请求会 500

- 机制：同一事务从 `engine.py:63` 的第一次 `flush`（UPDATE `analysis_tasks`）开始持有 SQLite 写锁，直到 `session_scope` 的 commit（`db.py:67`）；期间所有 Agent 的 LLM 调用（单次超时 60s，最多 3 次尝试：`config.py:38-39`、`llm.py:107`）都在事务内。
- 实测：第二个会话执行 `UPDATE analysis_tasks` 在约 5.5s 后抛 `sqlite3.OperationalError: database is locked`（pysqlite 默认 `timeout=5.0`，`db.py:24-29` 未覆盖该参数）。
- 影响面（按写入点列举，均为【代码可证】）：分析进行中，其他请求的写操作会一并撞锁 —— `create_resume`（`resumes.py:107`）、`create_jd`（`jds.py:35`）、`create_task`（`tasks.py:57`）、`get_current_user` 首次建用户（`deps.py:27`）、`create_batch`（`hr.py:122`）、反馈/支付等。这些异常最终由兜底处理器变成 `500 INTERNAL_ERROR`（`main.py:79-85`），且**不会**被记录到任务状态。
- 读操作不受影响（WAL + 快照读），实测轮询在写事务进行中仍可返回。

### 8.3 【代码可证】异常逃出事务块 → 回滚 + 静默吞异常 → 任务永久 `pending`

- `session_scope` 在异常时 `rollback()` 并重抛（`db.py:68-70`）。`run_workflow` 内有多处可能抛出且**不在** try 内：`rag.search`（`engine.py:77`）、`prompts.load_overrides`（`engine.py:71`）、`analyze.compute_scores`（`engine.py:118`）、`_persist_report`（`engine.py:119`，含逐条 findings 写入 `185-199`）。
- 一旦发生：本事务内所有 `flush`（含 `status="running"`、各步骤行、进度）**全部回滚**，任务在库里仍是创建时的 `pending`；`launch_workflow` 只 `traceback.print_exc()`（`engine.py:42-43`），不写 `error`、不置 `failed`。
- 前端后果：`status` 一直 `pending`，轮询永不终止（`page.tsx:84-86` 无条件续期），用户永久停留在「任务排队中 0%」。后端也无超时兜底/僵尸任务清理。
- 对比：Agent 自身异常是被显式处理的（`engine.py:101-106`），所以该风险集中在「引擎自身的非 Agent 代码」上。

### 8.4 【代码可证】`degraded` 是后端契约内的状态，但前端类型与文案表都没有它

- 后端会写 `status="degraded"`（`engine.py:97`），并在序列化时原样透传（`serializers.py:106`，`models.py:188-189` 的注释也列出该值）。
- 前端：`StepStatus` 只声明 `'pending' | 'running' | 'done' | 'failed'`（`lib/types.ts:293`）；`STATUS_TEXT` 也没有 degraded 键（`step-timeline.tsx:68-73`），渲染时 `STATUS_TEXT[step.status]` 得到 `undefined`（React 渲染为空），图标落到默认虚线圆（`step-timeline.tsx:61-65`），连接线按「非 done」处理（`:109`）。
- 触发条件：live 模式下某 Agent 的 LLM 调用连续失败（`llm.py:106-131`），或非 critical Agent 抛异常（`base.py:97-104`）。mock 模式不会出现（`llm.py:90-104` 返回 `degraded=False`）。
- 即：**用户会看到「一步没有状态文字、看起来像还没开始」，而任务其实已经带着降级产物跑完并生成报告**。

### 8.5 【代码可证】重复触发 / 幂等 / 配额

- `POST /api/v1/tasks` 没有幂等键、没有「同 resume+jd 进行中任务」去重（`tasks.py:29-63`）：双击或重试会创建多条任务，各扣一次免费额度（`tasks.py:56` 在 commit 前无条件自增）。
- 配额只在前端做按钮禁用（`page.tsx:68,73,263`）；后端每请求独立判定（`tasks.py:44`），并发两次请求都可能通过闸门（**推断**：读-改-写不是原子的，SQLite 下也会受 8.2 的锁影响）。
- 配额在任务创建时即消耗，**失败不返还**（全代码无 `free_quota_used` 回退逻辑：仅 `tasks.py:56` 与 `deps.py:25` 写入）。
- 另：`GET /tasks/{id}` 用 `scalar_one_or_none()` 取报告（`tasks.py:95`）——若同一任务被跑两次（`run_workflow` 是公开导出函数，`workflow/__init__.py:3-5`；`hr.py:72`、`scripts/*` 都会直接调用），会产生两行 `analysis_reports`，该查询将抛 `MultipleResultsFound` → 500。当前单入口流程不会触发，**【推断】** 仅在重复执行时成立。

### 8.6 【代码可证】简历/JD 缺失与空文本

- 记录缺失：`task.resume_id/jd_id` 为 NULL（列可空，`models.py:111-112`）或行已被删除（`resumes.py:235` 级联删除任务）→ `_fail("简历或 JD 记录缺失")`（`engine.py:54-58`）。级联删除意味着「删除简历」也会删掉历史任务与报告，正在跑的任务随后会在下一次 `flush` 时报错（**推断**：SQLite 外键 ON + 行已删 → UPDATE 影响 0 行，SQLAlchemy 可能抛 StaleDataError）。
- 空文本：抽文为空是正常分支（`extract.py:45-47`；无 OCR 时 `:64-70` 返回空文本 + note），`create_resume` 仍落库（`resumes.py:92-105`），`run_workflow` 用 `resume.raw_text or ""`（`engine.py:67`）继续跑；`parse` 是 critical 但规则解析空文本不会抛异常（`parse_agent.py:62-81`，mock 与 live 都能返回空结构）。结果：**一份无文本的扫描件会「成功」产出低分报告**，错误只在 `resumes` 接口返回的 `extract_note` 里（`resumes.py:112`），而首页并不消费该字段（`frontend/app/page.tsx:87-97`）。
- 空 JD：接口层有 20 字下限（`schemas.py:25-33`），但历史数据/直连 DB 写入的 JD 仍可能为空 → 检索 query 为空时跳过 RAG（`engine.py:76`），流水线继续。

### 8.7 【代码可证】前端错误路径不会停止轮询

`page.tsx:78-86`：`catch` 里只 `setError`，随后仍执行 `timer = setTimeout(poll, …)`。因此：

- 任务 ID 不存在（`TASK_NOT_FOUND`，`tasks.py:92-93`）时会**永久**每 1.5s 打一次 404；
- 后端重启/网络闪断时同样不会退避（无指数退避、无最大次数）；
- 用户点「重新查询状态」只是把 `retryToken` 加一，触发 effect 重建（`page.tsx:39,122,137-140`），旧循环被 `clearTimer` 清掉（`:91-94`），行为正确但不会消除 404 场景的持续轮询。

### 8.8 【代码可证】「20–60 秒」与真实上界不一致

- UI 承诺：`page.tsx:118`、`frontend/app/page.tsx:279` 均称 20–60 秒。
- 代码给出的单步上界：`llm_timeout_s=60`（`config.py:38`）× 尝试次数 `max_retries+1 = 3`（`config.py:39`、`llm.py:107`）+ 退避 `min(2**(attempt-1),4)` 共 1+2=3s（`llm.py:114`）≈ 183s/步；8 步串行（`engine.py:81`）且**每步恰好一次 `llm.complete_json` 调用**（8 个调用点：`parse_agent.py:76`、`jd_match_agent.py:60`、`ats_agent.py:48`、`impact_agent.py:48`、`risk_agent.py:55`、`suggestion_agent.py:43`、`report_agent.py:53`、`qc_agent.py:67`），最坏可达 20 分钟量级。前端没有任何超时兜底。
- 另：`httpx.AsyncClient` 是**每次 LLM 调用新建**（`llm.py:153`），无连接复用。

### 8.9 【代码可证】同一事实的三处「8」硬编码

`TOTAL_STEPS = len(AGENT_PIPELINE)`（`engine.py:35`，进度分母）、`serializers.STEP_ORDER`（`serializers.py:112`，API 的 `total_steps` 与补行顺序）、前端 `STEP_ORDER` + `StepName` 联合类型（`step-timeline.tsx:6-15`、`lib/types.ts:283-291`）。此外 `engine.py:116,128` 把 `"qc"` 硬编码为「触发报告落库的最后一步」和收尾 `current_step`。任何流水线增删改序都会同时打破进度分母、API 步数、前端渲染与报告落库触发点（**代码可证的结构性风险**）。

### 8.10 【代码可证】持久化与生命周期

- 建表靠 `Base.metadata.create_all`（`db.py:45-49`，`main.py:28`），无迁移工具；加列不会自动生效。
- 启动时灌知识库种子（`main.py:29-32`，`services/seed.py`）。
- 后台任务**没有队列、没有持久化调度**：任务行落库后由进程内线程执行，进程重启即丢失在途工作（任务永远停在 `pending`），也没有启动时的僵尸任务回收（全仓库无相关代码）。
- `get_llm()` 是模块级单例（`llm.py:188-195`），在多个后台线程间共享；`LLMClient` 只持有配置、`AsyncClient` 按调用创建（`llm.py:70-73,153`），因此**当前**是线程安全的【代码可证】；若将来在客户端内缓存连接/状态，该单例会变成跨线程共享可变状态（**推断**，仅为前瞻提示）。

### 8.11 【代码可证】其它一致性问题

- `extra_json.agent_outputs.report.scores` 是 QC **之前**的分数，与报告顶层分数不同源（见 4.1）。
- `top_k=5` 硬编码（`engine.py:77`）而 `settings.rag_top_k`（`config.py:45`）未使用。
- `tasks.py:60` 注释描述的 `await` 语义与 `engine.py:38` 的同步函数不符（见 2.3）。
- `WorkflowStep` 无 `updated_at`/`completed_at`（`models.py:182-199`），步骤「何时结束」只能靠 `created_at + latency_ms` 反推。
- `serialize_step` 把 `cost` 转 `float`（`serializers.py:108`），而 `progress`/`cost` 列是 `Numeric`（`models.py:119,193`）：SQLite 下由驱动做数值转换，换 PostgreSQL 后类型为 `NUMERIC`，`float()` 仍可用，但精度/表示会变化（**推断**，未做迁移验证）。

---

## 9. 明确存疑 / 未验证的部分

1. 未启动 FastAPI 服务做真实 HTTP 端到端验证：第 8.1/8.2 的结论来自「用项目自身的 `db.SessionLocal`/`session_scope` 与真实 `run_workflow`」的进程内复现，而非走 uvicorn + 浏览器；事务与派发语义与线上一致，但未覆盖反代/多 worker 场景。**多 worker（`uvicorn --workers>1`）时**，任务行与后台线程仍在同一 worker 内，事务结论不变；worker 崩溃的影响（在途任务丢失）未测。
2. 未审阅 `frontend/app/report/[reportId]/page.tsx` 与报告渲染组件，第 1-⑩ 只确认路由与 `GET /reports/{report_id}` 接口存在。
3. 未运行 live 模式（未配置 DeepSeek Key），因此 degraded 步骤、真实 tokens/cost、重试退避的实际表现均来自代码推导；mock 模式的 end-to-end 已验证。
4. `AnalysisTask.progress` 为 `Numeric(5,4)`：SQLite 下实测读写往返正常（返回 0.125/1.0），但不同驱动是否出现 Decimal/浮点舍入差异未系统验证。
5. 未检查 `docker-compose.yml`/`Dockerfile` 的部署形态（是否多 worker、是否 PostgreSQL），因此 8.1/8.2 的严重度在 Postgres 部署下会变化：Postgres 的 READ COMMITTED **同样**使未提交写入对外不可见（8.1 结论不变），但 8.2 的「写锁阻塞」会变成行级锁 + 长事务（影响面不同、不一定是 5s 超时报错）。此判断为**【推断】**，未在 Postgres 上验证。
