# ZhenResume（简甄）× Dify + LangGraph 混合架构方案

> 决策文档 · 版本 v1.0 · 对应代码基线：`master` @ `23b8dcc`（工作区含 `workflow/engine.py` 未提交修改）
> 前置事实：迁移前基线自检 **174/174 全绿**（`python -m scripts.verify`，未配置 LibreOffice 时的口径）。
> 本方案的第一原则：**这份基线在改造后必须仍然是全绿**，否则改造不成立。

> ⚠️ **决策更新**：本文「能力层 = Dify」部分**已被否决**。项目改为**全代码路线**（LangGraph + pgvector + Langfuse，不引入任何低代码平台），最新实施指南见 [ZhenResume_LangGraph接入与并发实现.md](ZhenResume_LangGraph接入与并发实现.md)。
> 本文**仍然有效**的部分：§1 现状盘点、§2 职责矩阵与文件映射、§3 反模式黑名单、§4.1 LangGraph 编排层设计、§6 契约不变性清单、§8 风险登记册（与 Dify 相关的 R13–R17 可忽略）。
> Dify 侧调研结论保留在 [Dify架构调研-原始记录.md](Dify架构调研-原始记录.md)，作为「为什么不用低代码平台」的举证材料（Code 节点限制、run 不幂等、授权条款等）。

---

## 0. 结论先行

### 0.1 一句话结论

**不要把整个项目"搬进" Dify，也不要让 Dify 和 LangGraph 同时编排同一条链路。**

正确的混合形态是三层分工：

| 层 | 归属 | 职责 | 是否变动 |
|---|---|---|---|
| **契约层 / 业务层** | **FastAPI（保留）** | 端点、鉴权、配额、计费、任务与报告落库、序列化、导出、隐私删除 | **零改动**（新增内部模块，不改对外行为） |
| **编排层** | **LangGraph（替换 `workflow/engine.py`）** | 8 节点状态机、断点续跑、并行、重试策略、质检回环、人工介入 | 只换这一个文件 + 新增 `workflow/` 子模块 |
| **能力层** | **Dify（新增，可开关）** | 知识库 RAG 增强（真实向量 + rerank）、知识库运营、模型网关、非主链路运营型应用 | 新增，`DIFY_ENABLED=false` 时完全不参与 |

一句话概括：**LangGraph 管"怎么跑"，Dify 管"知道什么、用什么模型、运营怎么改"，FastAPI 管"对外长什么样"。**

### 0.2 为什么是"混合"而不是二选一

- **只用 Dify**：`services/analyze.py` 的确定性五维评分、`services/evidence.py` 的字符区间回定位、8 步 `workflow_steps` 成本核算、`X-Device-Id` 归属校验、DOCX/PDF 导出都必须留在 Python。这些一旦外移到 Dify 的 Code 节点，项目最核心的两条设计铁律（**评分不给 LLM**、**证据不信任模型定位**）会直接失效，且 `scripts/verify.py` 里对应断言全部失去意义。另外三条 Dify 1.17.x 的硬约束会立刻咬人：**Code 节点执行超时默认 15 秒**、输出有结构上限（对象数组 ≤30 元素、字符串 40 万字符等，**超限直接失败而不是截断**）、**workflow run 既没有幂等键也没有 async 模式**（官方 CLI 文档原文 "since a run isn't idempotent"；社区加 `Idempotency-Key` 与 `response_mode=async` 的提案均被 not planned 关闭）。把确定性资产与长流程放到 Dify 上，等于同时放弃可回归性与可重试性。
- **只用 LangGraph**：不会带来任何检索质量提升。README §八 已明确列出"换真实语义向量"是对检索质量提升最大的一步 —— 而这正好是 Dify 现成的能力（内置知识库 + 可插拔 embedding/rerank）。自己接 Ollama/bge-m3 + pgvector 是一条要写的活，Dify 是配置出来的活。
- **两者都对主链路负责 = 反模式**：会出现双编排、双 prompt 真源、双观测、编排状态两份、成本核算对不上，而且两边的失败语义（Dify run 不幂等 / LangGraph 节点可重放）无法统一。本方案用 **职责矩阵**（§2.1）和 **反模式黑名单**（§3）把这条锁死。

### 0.3 三档方案与推荐

| 档位 | 形态 | 改动面 | 收益 | 结论 |
|---|---|---|---|---|
| **A（推荐）** | LangGraph 接管主链路编排；Dify 只做**能力层**（知识检索 API + 知识库运营 + 非主链路应用） | `engine.py` 替换 + 新增 router/nodes + `services/rag.py` 增加 Dify retriever + 部署加 1 套 Dify | 断点续跑、并行降延迟、可重试策略、知识检索质量、知识库运营可视化 | **做这个** |
| B（折中） | 主链路完全不动；只把**非主链路**（报告解读问答、面试题生成、HR 摘要、知识库文档加工）放到 Dify | 纯新增，零改造 | 快速验证 Dify 价值，风险最低 | 先做一个月，再决定要不要上 A |
| C（不推荐） | 全量迁移到 Dify Workflow，8 个 Agent 变成 8 个 Dify 节点/应用 | 全部重写，契约需重新定义 | 可视化编排 | **不做**，见 §3 反模式 |

> 建议路径：**B → A**。先花 1–2 周用 B 档验证"Dify 在我们的知识库上是否真的更好、运营是否真的更省事"，用数据决定是否投入 A 档。直接上 A 也可以，但 B 能让失败成本趋近于零。

### 0.4 三条不可让渡的红线（贯穿全文）

1. **对外契约冻结**：`docs/api-contract.md`、`docs/api-contract.md §7` 的 `ParsedResume/ParsedJd`、15 张 ORM 表、前端硬编码的 8 个 `StepName` 与 1.5s 轮询节奏，全部不动。
2. **确定性资产原地不动**：评分、证据定位、规则解析、成本核算、降级链继续由 Python 承担，LangGraph 只负责"什么时候调用它们"，Dify 只负责"给它们提供素材"。
3. **降级链不退化**：`DIFY_ENABLED=false`、无 API Key、Dify 挂掉时，`mock` 模式仍必须产出契约合规的完整报告 —— 这是 `verify.py` 的既有断言。

---

## 1. 现状盘点：哪些能搬、哪些不能搬

### 1.1 主链路现状（事实）

```
POST /api/v1/tasks  (api/tasks.py, 202)
  └─ BackgroundTasks.add_task(launch_workflow, task_id)
        └─ workflow/engine.py::launch_workflow → asyncio.run(run_workflow(task_id))
              ├─ 读取 resume.raw_text / jd.raw_text / parsed_json
              ├─ prompts.load_overrides(db)  → ctx.prompt_overrides
              ├─ rag.build_query(...) + rag.search(db, query, top_k=5) → ctx.knowledge_hits   ← 一次性检索，全链路复用
              └─ for agent in AGENT_PIPELINE:                # 8 个，串行
                   ├─ 落一行 workflow_steps (status=running, input_json=摘要)
                   ├─ await agent.safe_run(ctx, llm)
                   ├─ 回填 status/tokens/cost/latency_ms/output_json
                   ├─ task.progress = index/8
                   └─ if agent.name == "qc": 重算分数 + 落报告(RequestReport + ReportFinding)
```

关键实现事实（改造时必须逐条保住）：

| # | 事实 | 位置 | 为什么不能动 |
|---|---|---|---|
| F1 | `qc` 之后**重算一次分数** | `engine.py:116-120` | 质检会剔除/降级发现项，不重算则总分与问题清单自相矛盾 |
| F2 | `workflow_steps.input_json` **只存摘要**，不存简历全文 | `engine.py:204-218` | 隐私 + 库膨胀；Dify 侧若持久化 inputs 会破坏这条 |
| F3 | `rag` 检索**一次**，全链路复用 | `engine.py:74-77` | 8 个节点各检索一次会让 token 成本与延迟翻倍 |
| F4 | 非 critical 节点失败 → `degraded` 继续；`parse` 失败 → 任务 `failed` | `agents/base.py:93-104`、`engine.py:101-106` | `verify.py` 有"降级不静默"断言 |
| F5 | 每个节点落一行 `workflow_steps`，带 tokens/cost/latency | `engine.py:83-114` | 管理后台 `/admin/metrics` 与"单份成本 ≤ 0.5 元"验收项都靠它 |
| F6 | 前端硬编码 `STEP_ORDER`（8 个）与 `POLL_INTERVAL_MS=1500` | `frontend/components/step-timeline.tsx:6`、`frontend/app/analyze/[taskId]/page.tsx:16` | 步名/步数是硬契约，改一个前端就白屏 |
| F7 | `evidence.span` 是**简历原文的字符区间** | `services/evidence.py`、`contract §5.1` | 只能由 Python 在原文里重新定位，Dify 给不了 |
| F8 | embedding 是**本地确定性哈希向量**（DeepSeek 无 embedding 接口） | `services/embeddings.py:1-12` | 这是检索质量短板，也是 Dify 最实的收益点 |
| F9 | prompt 覆盖走 `prompt_templates` 表 + 版本自增 + `POST /admin/prompts` | `services/prompts.py`、`api/admin.py:120-141` | 契约 §6.3 已冻结；且"运行期改 prompt 不重启"是产品能力 |
| F10 | `mock` 模式零 Key 跑通全链路 | `services/llm.py:90-104` | 开发/演示/CI 的地基 |

### 1.2 节点语义（决定 LangGraph 状态怎么设计）

现有的 8 个 Agent **不是纯函数**：它们直接读写共享的 `AgentContext`。这一点决定了 LangGraph 状态 schema 的写法。

| 节点 | 写入方式 | 具体字段 |
|---|---|---|
| `parse` | 覆盖 | `parsed_resume`、`parsed_jd`、`role_family` |
| `jd_match` | **覆盖 + 过滤既有 findings** | `ctx.findings = [f for f in ctx.findings if not suppressed]`，再 `add_findings`；`missing_keywords`、`covered_keywords` |
| `ats` | 追加 | `ctx.add_findings(...)` |
| `impact` | 追加 | `ctx.add_findings(...)` |
| `risk` | 追加 | `ctx.add_findings(...)` |
| `suggestion` | **整体替换** | `ctx.findings = findings`、`action_items` |
| `report` | 覆盖 + 计算 | `ctx.scores`（首次定稿）、`knowledge_refs`、`summary` |
| `qc` | **整体替换** | `ctx.findings = kept`、`qc_report` |
| （引擎，非节点） | 覆盖 | `qc` 后重算 `scores`（F1） |

**由此得到两条硬结论**（§5.1 会展开）：

- **不要**给 `findings` 加 `operator.add` reducer。追加/替换语义混用（`grep 'ctx.findings ='` 有 3 处整体替换）会让 reducer 把"替换"变成"再追加一份"，报告里立刻出现重复问题项。
- 只有**并行扇出**那一版才需要 reducer，且必须让并行节点写**独立的批次字段**（`finding_batches`），再由一个确定性归并节点按固定顺序展平 —— 否则发现项顺序会随调度抖动，`sort_findings` 的输出不再稳定。

### 1.3 不能被外包的五类确定性资产

| 资产 | 文件 | 契约/验收依赖 |
|---|---|---|
| 五维评分公式（同输入必同分） | `services/analyze.py::compute_scores` | `verify.py`「同一简历两次诊断分数一致（可回归）」 |
| 证据引文定位（不信任模型给的坐标） | `services/evidence.py::locate_quote` | 「每条红色风险都有可定位原文引用」 |
| 规则解析基线（章节/条目/日期/技能） | `services/parse_rules.py` | 编辑器↔上传简历等价性断言 |
| 公平性拦截（歧视性内容） | `agents/risk_agent.py` + `agents/qc_agent.py` | 「公平性检查通过率 100%」 |
| 成本核算与降级链 | `services/cost.py`、`services/llm.py` | 「单份成本 ≤ 0.5 元」、mock 零 Key 跑通 |

> **判断口径**：一个能力如果"同一输入必须给出完全相同的输出"，它就不能交给 Dify 的 LLM 节点或 Code 节点 —— Code 节点看起来是代码，但它的沙箱、超时、可用库、日志都与主仓库脱钩，等于把确定性资产搬到一个无法做代码审查与单测的地方。

---

## 2. 目标架构

### 2.1 职责矩阵（谁拥有什么 —— 防双写的第一道闸）

| 能力 | 唯一真源 | 说明 |
|---|---|---|
| 对外 API 契约与响应结构 | **FastAPI** | Dify 的任何返回都必须被映射回现有结构，禁止直接透传 |
| 任务状态机（pending/running/done/failed）、progress、current_step | **FastAPI 业务表** | `analysis_tasks` 不变 |
| 8 步 `workflow_steps`（tokens/cost/latency） | **FastAPI 业务表** | LangGraph 节点把用量回填进来；Dify 的用量只作为对账参考 |
| 评分 / 证据 / 规则解析 / 公平性 | **Python 服务层** | LangGraph 只调度，Dify 不参与 |
| 执行顺序、重试、并行、回环、断点续跑 | **LangGraph** | 这是本次替换的全部内容 |
| 知识库**文档真源**（`KnowledgeDoc`/`KnowledgeChunk`） | **FastAPI 业务表** | §4.5 会说明为什么不让 Dify 当知识库真源 |
| 知识库**检索**（向量化、rerank、召回） | **Dify（可开关）**，本地混合检索为 fallback | 契约 §6.2 的 `/knowledge/search` 响应结构不变，只换检索引擎 |
| Prompt 代码默认值 | **`services/prompts.py`** | 不变 |
| Prompt 运行期覆盖 | **`prompt_templates` 表（业务库）** | Dify Prompt IDE 只作为"试验场"，定稿后经既有 `POST /admin/prompts` 写回；见 §4.2 |
| LLM 调用与降级 | **`services/llm.py`** | Dify 作为**可选第二 provider**，不是必经路径 |
| 运营型 LLM 应用（报告问答、面试题、HR 摘要） | **Dify** | 与主链路解耦，挂了不影响主链路 |

**唯一的双写点**（必须显式承认并设计好）：知识库文档从业务库同步到 Dify dataset。策略见 §4.5。

### 2.2 数据流（目标态）

```
                        ┌─────────────────────────── FastAPI 契约层（不变）───────────────────────────┐
POST /tasks ──202──►    │ analysis_tasks(status=pending)  →  BackgroundTasks.launch_workflow(task_id)  │
                        └───────────────────────────────────┬──────────────────────────────────────────┘
                                                            │  thread_id = task_id
                        ┌───────────────────────────────────▼──────────────────────────────────────────┐
                        │ LangGraph 编排层（新增）                                                  │
                        │                                                          checkpointer       │
                        │  setup ─► retrieve ─► parse ─► jd_match ─┬─► ats ─┐                        │
                        │  (取库、  (rag/dify)  (critical)          ├─► impact├─► merge ─► suggestion   │
                        │   装 state)                              └─► risk ─┘   (确定性归并)          │
                        │                                        （并行版；串行版这里是直线）        │
                        │  suggestion ─► report ─► qc ─► [条件] ─┬─► finalize_scores ─► persist ─► END │
                        │                                       ├─► rework(≤N 次)  ──┘                │
                        │                                       └─► human_review (interrupt，可选)    │
                        └───────┬────────────────────────────────────────────────────┬─────────────────┘
                                │ 每步 upsert workflow_steps + 更新 progress          │ HTTP
                                ▼                                                    ▼
                     ┌──────────────────┐                            ┌──────────────────────────┐
                     │ 业务库（SQLite/PG）│                           │ Dify（能力层，可开关）    │
                     │ 15 张表不变        │                           │ · Dataset 检索 API        │
                     │ + 唯一索引(task_id,│                           │ · 模型供应商 / 网关        │
                     │   step_name)      │                           │ · 运营型 Workflow 应用     │
                     │ + graph checkpoint │                           │ · 日志与标注（须做隐私配置）│
                     └──────────────────┘                            └──────────────────────────┘
```

前端的 `GET /tasks/{id}` 轮询、`GET /reports/{id}` 结构与节奏 **完全不变**。

### 2.3 文件级改造映射

| 文件 | 处置 | 说明 |
|---|---|---|
| `backend/app/workflow/engine.py` | **改造**（保留 `launch_workflow` / `run_workflow` 签名） | 内部改为构建并执行 LangGraph 图；保留 `WORKFLOW_ENGINE=legacy` 开关走老实现 |
| `backend/app/workflow/graph.py` | 新增 | `build_graph()`、节点注册、边、编译（带 checkpointer） |
| `backend/app/workflow/state.py` | 新增 | `WorkflowState` / `StepRecord` 定义与 `AgentContext ↔ state` 适配 |
| `backend/app/workflow/nodes.py` | 新增 | 把现有 8 个 Agent 包成图的节点；`setup/retrieve/merge/finalize/persist/human_review` 等非契约节点 |
| `backend/app/workflow/steps.py` | 新增 | `workflow_steps` 幂等 upsert（`(task_id, step_name)`）与 progress 计算 |
| `backend/app/workflow/recorder.py` | 新增（可选） | 用量/耗时统一回填 |
| `backend/app/services/dify.py` | 新增 | Dify Service API / Dataset 检索 API 客户端；超时、重试、熔断、错误映射 |
| `backend/app/services/rag.py` | **改造** | 抽出 `Retriever` 接口：`LocalHybridRetriever`（现状） / `DifyRetriever`（新增），由 `RAG_BACKEND` 选择 |
| `backend/app/services/embeddings.py` | 保留 | 本地 fallback 与 mock 模式的向量来源 |
| `backend/app/services/llm.py` | 保留（可扩展） | 保持单一入口与三重降级链；Dify 网关作为可选 provider 分支 |
| `backend/app/services/prompts.py` | 保留 | 真源不动；Dify 只做试验场 |
| `backend/app/agents/*` | **零改动** | 这是本方案能低风险落地的前提 |
| `backend/app/services/analyze.py` / `evidence.py` / `parse_rules.py` / `cost.py` | **零改动** | 确定性资产 |
| `backend/app/api/*` | **零改动** | 契约层；Dify 相关运维端点在 P4 起以**新增**方式加入并同步更新契约文档 |
| `backend/app/models.py` | **加不改** | 新增唯一索引 `uq_workflow_step(task_id, step_name)`；新增 `knowledge_sync_state` 表（可选） |
| `docs/api-contract.md` | 只在新增端点时更新 | 遵循项目"改接口先改契约"的约定 |
| 前端 | **零改动** | 除非要展示人工复核状态（P5 可选） |
| `docker-compose.yml` | 新增一份 `deploy/dify/` compose | 不动现有两份服务定义；Dify 独立 compose + 独立网络 |

---

## 3. 为什么必须"混合"：重叠区与反模式黑名单

Dify 的 Workflow 与 LangGraph 在"编排"这件事上**功能高度重叠**。混合架构成立的前提是：**同一条链路上只能有一个编排者**。本方案的选择是主链路归 LangGraph，Dify 退到能力层。

### 3.1 九个典型反模式（逐条给后果）

| # | 反模式 | 后果（具体到本项目） |
|---|---|---|
| A1 | 8 个 Agent 各做成一个 Dify 应用，FastAPI 串行 HTTP 调用 | 8 次跨进程往返；**注意：Dify 1.17.x 的 `inputs` 其实已支持任意 JSON（`dict[str, Any]`，且有 `JSON Code` 变量类型 + JSON Schema 校验），所以"传不了嵌套结构"不是理由**；真正的问题是：`workflow run` **没有幂等键**、没有 async 模式（blocking 占住连接、streaming 要自己消费 SSE 与 ping 心跳帧）、每步还要自己回写 `workflow_steps`、`parsed_resume` 会落进 Dify 的 run 日志（默认不清理） |
| A2 | 把 `compute_scores` / `locate_quote` 做成 Dify Code 节点 | 确定性资产离开仓库与单测覆盖；Code 沙箱里没有 `ParsedResume` 模型与既有工具函数；`verify.py` 的"分数可回归""引文可定位"两条断言失去保护对象 |
| A3 | Prompt 真源搬到 Dify，同时保留 `prompt_templates` | 双真源，线上到底用哪份说不清；`POST /admin/prompts` 立刻变成"改了不生效"的假接口（契约 §6.3 被破坏） |
| A4 | 把简历全文/JSON 作为 `inputs` 传给 Dify 应用 | Dify 默认会把它自己应用的输入输出落到自己的数据库里（日志/追踪），**简历原文（含手机号、邮箱）从此多了一份不受 `DELETE /resumes/{id}` 管辖的副本**，与 `aaa.md §4` 的隐私要求直接冲突 |
| A5 | Dify 与 LangGraph 都能改同一份任务状态 | `progress`/`current_step` 两个写者，前端轮询会出现"进度倒退"或"步骤名不在 8 个之内"→ 前端 `STEP_ORDER` 映射失败 |
| A6 | 让 Dify 也编排主链路的某几步（"一半在 Dify、一半在 LangGraph"） | 双重编排、两个事实来源；Dify 的 DSL 只覆盖一半流程，CI 里无法整体回滚；出错时无法判断是哪一侧的状态机错了 |
| A7 | 把 Dify 当业务 of record（任务状态、报告、配额写进 Dify） | Dify 的 run 数据在自己的库（`workflow_runs` / `node_executions` / `app_logs`），**日志默认不清理**（`WORKFLOW_LOG_CLEANUP_ENABLED=false`），云版还会归档成 CSV；业务查询与级联删除会被迫跨系统 |
| A8 | 把 Dify 的 `/console/api/*` 当集成面 | 那是控制台内部接口，无文档、无版本承诺（历史上还出现过 GET 有副作用的问题）→ 只允许用 `/v1/*` 的 Service API |
| A9 | 把 Dify 应用放在一个会自动重试的链路上 | run 不幂等 → 自动重试会重复执行 LLM 调用与计费 |

### 3.2 允许的重叠（互补而非竞争）

| 场景 | 谁做 | 为什么允许 |
|---|---|---|
| 知识检索 | Dify 提供向量+rerank，LangGraph 节点调用 | 一个提供能力、一个决定时机，不冲突 |
| 运营型应用（报告问答/面试题/HR 摘要） | Dify 独立应用，FastAPI 代理调用 | 与主链路无共享状态，挂了可降级 |
| Prompt 试验 | Dify Prompt IDE 调参 → 人工把定稿写回 `POST /admin/prompts` | 单向流动，真源唯一 |
| 观测 | Dify 看"它自己那些调用"，业务看 `workflow_steps` | 两个观测面覆盖不同范围，不互相冒充 |

---

## 4. 详细设计

### 4.1 LangGraph 编排层（替换 `workflow/engine.py`）

#### 4.1.1 状态契约

LangGraph 的 state 必须是**可 JSON 序列化的普通数据**（否则 checkpointer 存不下）。现有 `AgentContext` 恰好满足（字段全是 str/int/list/dict/`ParsedResume`），因此适配层几乎是机械的。

```python
# backend/app/workflow/state.py（新增）
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
import operator


@dataclass
class GraphContext:
    """非序列化依赖走 Runtime context，绝不进 state（LangGraph 1.x 的 context API）。"""

    llm: Any                    # services.llm.LLMClient
    retriever: Any              # services.rag.Retriever
    db_factory: Any = None      # 需要独立 session 时用


class StepRecord(TypedDict, total=False):
    step_name: str
    status: str        # running | done | degraded | failed
    tokens: int
    cost: float
    latency_ms: int
    notes: str | None
    input_digest: dict
    output_digest: dict
    contract_step: bool          # False = 非契约节点，不写 workflow_steps、不计入 progress

class WorkflowState(TypedDict, total=False):
    # 输入（setup 节点一次性装入）
    task_id: str
    resume_text: str
    jd_text: str
    parsed_resume: dict
    parsed_jd: dict
    prompt_overrides: dict[str, str]
    # 检索（retrieve 节点写入一次，全链路复用 —— 保住 F3）
    knowledge_hits: list[dict]
    # 逐层累积：**不加 reducer**，节点返回完整值 → 与现状的"追加/替换"语义 1:1
    findings: list[dict]
    missing_keywords: list[str]
    covered_keywords: list[str]
    action_items: list[str]
    knowledge_refs: list[dict]
    scores: dict
    summary: str
    qc_report: dict
    outputs: dict
    role_family: str
    # 仅并行版使用：并行节点各自 append 一个批次（这个必须加 reducer）
    finding_batches: Annotated[list[list[dict]], operator.add]
    # 审计与用量回流（必须加 reducer：并行节点各写一条）
    steps: Annotated[list[StepRecord], operator.add]
    rework_count: int
    review_decision: str | None
```

**三条状态设计铁律**（直接来自 §1.2 的代码事实）：

| 铁律 | 原因 | 不遵守的后果 |
|---|---|---|
| `findings` **不加** `operator.add` | `jd_match` / `suggestion` / `qc` 是**整体替换**语义 | 报告里出现重复问题项，`发现项 ≥ 5 条` 与排序断言失真 |
| 并行节点写**独立批次字段** `finding_batches` | 三者只做 append，互不替换 | 用 reducer 直接写 `findings` 会让后续替换变成追加 |
| 归并节点按**固定顺序**展平 | `verify.py` 断言「发现项按 red→yellow→green 排序」 | 发现项顺序随调度抖动，回归不可比 |

**禁止放进 state 的东西**：`Session`、`httpx.AsyncClient`、`LLMClient`、`AgentContext` 实例本身。前三个放 `Runtime.context`（每次调用注入），第四个是 dataclass，序列化后再构造即可。

#### 4.1.1b 版本与 API 基线（已核查：LangGraph 1.x）

| 事实 | 值 / 出处 | 对方案的影响 |
|---|---|---|
| 当前版本 | `langgraph` 1.2.x（PyPI 最新 1.2.15），**MIT**，`requires_python>=3.10` | 本项目 Python 3.10.8 满足；但见下方 3.10 限定 |
| checkpointer 包 | `langgraph-checkpoint-sqlite`（3.1.x）、`langgraph-checkpoint-postgres`（3.2.x），**均为 MIT** | 单机 SQLite、生产 Postgres，两条路都可用 |
| Postgres checkpointer 的驱动 | **psycopg 3 + psycopg-pool**（不是 asyncpg） | `requirements.txt` 增加 `psycopg[binary]`；与现有 SQLAlchemy 的 psycopg 方言方向一致 |
| 上下文注入（1.x 新 API） | `StateGraph(State, context_schema=Ctx)` + 节点签名 `(state, runtime: Runtime[Ctx])` + `invoke(..., context=Ctx(...))`；而 `thread_id` 仍走 `config["configurable"]` | 第 5 节骨架按此写：`LLMClient` 走 `runtime.context`，**不进 state** |
| 首次建表 | Postgres checkpointer 必须先调 `.setup()`；手工建连接要 `autocommit=True` + `row_factory=dict_row` | 放在 `main.py` 的 lifespan，且必须幂等 |
| `thread_id` 长度 | Postgres 下必须 < 255 字符 | 本项目用 uuid，安全 |
| durability | `exit` / `async` / `sync` 三档（`exit` 最快但**进程崩溃无法中间恢复**） | 建议 `async`；要"崩溃必可续跑"就 `sync`（更慢） |
| 序列化 | 默认 `JsonPlusSerializer`（msgpack+JSON），**不支持任意对象** | 再次印证：`Session` / `httpx.AsyncClient` 不能进 state |
| 安全开关 | 官方建议 `LANGGRAPH_STRICT_MSGPACK=true`（限制反序列化白名单） | 生产必开 |
| Python < 3.11 的已知限制 | async 场景必须显式把 `RunnableConfig` 传进 `ainvoke`；`get_stream_writer()` 不可用（改为节点 `writer: StreamWriter` 参数） | 本项目 3.10.8 **命中**；我们不依赖 token 级流式，影响可控，但要用 `PYTHONUTF8=1` |
| Windows | 官方 CI 至今无 Windows runner；`langgraph dev` 曾在 GBK 中文 locale 下抛 `UnicodeDecodeError`（官方 issue 已关闭未修，workaround `PYTHONUTF8=1`） | **不使用 `langgraph dev` / `langgraph-cli`**；启动脚本设 `PYTHONUTF8=1` |
| Agent Server / Platform | `langgraph-api` 为 **Elastic License 2.0**（非 MIT），生产使用需 `LANGGRAPH_CLOUD_LICENSE_KEY` 与到 `beacon.langchain.com` 的外联 | **本方案完全不用**（§4.1.8） |

> 一句话：**LangGraph 只当库用（MIT），不碰 Platform/Agent Server**。这与本方案"三个开关就能回退"的设计天然一致。

#### 4.1.2 图结构：两版并存，串行版是默认

**A. 串行版（P2 默认，行为等价，用于"零回归"上线）**

```
START → setup → retrieve → parse → jd_match → ats → impact → risk
      → suggestion → report → qc → finalize → persist → END
```

**B. 并行版（P4 可选开关 `WORKFLOW_PARALLEL=true`）**

```
START → setup → retrieve → parse → jd_match ─┬→ ats ─┐
                                             ├→ impact ─┼→ merge → suggestion → report → qc → finalize → persist → END
                                             └→ risk ─┘
```

**C. 回环与人工介入（P4/P5 可选）**

```
qc ─┬─ rework_count < REWORK_MAX 且 qc 判 drop 比例超阈值 ─→ suggestion（重写一轮）
    ├─ 命中人工复核条件（P5） ─→ human_review（interrupt） ─→ suggestion / finalize
    └─ 默认 ─→ finalize
```

**硬规则：图节点数 ≠ 契约步数。**

| 图节点 | 是否契约步（写 `workflow_steps`） | 说明 |
|---|---|---|
| `parse` `jd_match` `ats` `impact` `risk` `suggestion` `report` `qc` | ✅ 是（8 个，顺序即契约） | 名字必须逐字对齐 `AGENT_PIPELINE[].name` |
| `setup` `retrieve` `merge` `finalize` `persist` `human_review` | ❌ 否（`contract_step=False`） | 不写 `workflow_steps`、不计入 `progress`。`finalize` 就是 F1 的"qc 后重算分数" |
| `rework` | ❌ 否 | 回环不是新步骤，否则 `总步数为 8` 断言失败 |

`progress` 的唯一算法：**已完成（done/degraded/failed）的契约步去重后的个数 ÷ 8**，最终必须精确等于 `1.0`（`verify.py`「progress 达到 1.0」）。并行版一次前进 3 步，前端进度条不会倒退。

#### 4.1.3 断点续跑与幂等

| 事项 | 设计 |
|---|---|
| 会话标识 | `thread_id = task_id`（业务主键直接当 thread，不引入新 id） |
| checkpointer | 开发/单机：SQLite（`langgraph-checkpoint-sqlite`）；生产：PostgreSQL（`langgraph-checkpoint-postgres`）。**与业务库可用同一个 PG 实例、不同 schema** |
| 恢复入口 | 不用新增 API：在 `main.py` 的 lifespan 里加一个"僵尸任务回收"钩子 —— 找出 `status='running'` 且 `updated_at` 超过阈值的任务，用同一 `thread_id` 重新 `ainvoke`。已完成的节点不会重跑 |
| `workflow_steps` 幂等 | **必须**新增唯一索引 `(task_id, step_name)` + `upsert`。否则节点重放会插入第二行，`步骤数为 8` 断言直接失败 |
| 报告幂等 | `persist` 节点按 `task_id` upsert `analysis_reports`，`report_findings` 先按 `report_id` 删除再插入（重放不产生重复发现项） |
| `task.progress` 幂等 | 由"已完成的契约步集合"推导，而非自增计数 |

**两个必须避开的坑**：

1. **event loop 绑定**：现状 `launch_workflow` 用 `asyncio.run(...)`，每次新建事件循环。若把 `AsyncXxxSaver` 做成模块级全局单例，第二次任务会拿到绑定在已关闭 loop 上的连接池 → 时报错。**对策**：checkpointer 与编译后的图**按任务构建**（构造成本极低），或在 P4 之后把工作流搬进单事件循环的独立 worker 进程（届时才值得做全局单例）。
2. **`PARSE` 失败语义**：现状是 `parse` 抛异常 → 整个任务 `failed`。并行版要在最外层捕获并从 checkpoint 落下"任务失败"，不能让节点异常把 LangGraph 的调用栈原样抛给 BackgroundTasks（那样任务会永远停在 `running`）。

#### 4.1.3b OSS 路径必须自建的三件事（Agent Server 才自带）

LangGraph 的 OSS 库**不包含** Agent Server 的三项生产保障。选 OSS 就要接受下面这张账单，并且把它写进 P3 的验收里：

| 能力 | Agent Server（Elastic-2.0，不用） | 本方案（OSS，MIT）的自建方式 |
|---|---|---|
| **同一 thread 不被并发执行** | 队列强制"1 run/thread" | 加锁：`analysis_tasks` 上一把行锁/乐观锁（`UPDATE ... WHERE status<>'running'`），或用 `threading.Lock` + DB 唯一约束。**这是硬需求**：同一 `thread_id` 并发写会让 checkpoint 互相覆盖 |
| **checkpoint TTL / 清理** | `checkpointer.ttl` 配置 | 自己写清理：`scripts/prune_checkpoints.py` + 定时任务（按 `created_at` 删旧 `checkpoints`/`checkpoint_writes`）。`verify.py` 之外加一条"表行数不随时间单调增长"的可观测指标 |
| **持久任务队列 / exactly-once** | 内置 Postgres 队列 | **本方案不引入队列**：继续用 `BackgroundTasks`（见下方约束），失败由 checkpoint 续跑兜底 |

> **一个必须写进设计文档的约束**：`scripts/verify.py` 的 174/177 项断言建立在"**请求返回时后台任务已经跑完**"之上（`verify.py:140` 的注释原文："TestClient 会在响应返回前跑完后台任务，因此这里应已 done"）。因此 **P2/P3 阶段绝不能把工作流挪到独立 worker 进程或真实队列**，否则「任务执行成功」「progress 达到 1.0」等断言会在没有等待逻辑的情况下失败。真要上队列，必须同步改造 `verify.py`（加轮询等待），并把这一步单独作为一个阶段。

#### 4.1.4 重试策略：只加在网络边界，不改降级语义

| 位置 | 由谁重试 | 说明 |
|---|---|---|
| LLM 调用（DeepSeek） | **`services/llm.py`（保持现状，指数退避 + 回落规则引擎）** | 绝不能改用节点的 `RetryPolicy` 替代，否则 `LLMResult.degraded` 不会被记录，`verify.py`「所有步骤均完成或降级」与"降级不静默"就形同虚设 |
| Dify 检索 | 节点级 `RetryPolicy(max_attempts=3)` + 熔断 → 回落本地检索 | 见 §4.2 |
| 业务库读写 | 不重试（让异常冒泡到任务失败） | 现在就是失败即止 |
| 单个 Agent 内部异常 | **`Agent.safe_run` 现状语义**（非 critical → degraded） | 保持不动 |

`RetryPolicy` 的具体语义（已核查）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_attempts` | 3 | **包含首次调用** |
| `initial_interval` / `backoff_factor` / `max_interval` | 0.5s / 2.0 / 128s | 指数退避，`jitter=True` 默认开 |
| `retry_on` | `default_retry_on` | **默认不重试** `ValueError/TypeError/RuntimeError/OSError` 以及 httpx/requests 的 **4xx**，只重试 **5xx** |

由此得到三条落地结论：

1. Dify 检索节点用默认 `retry_on` 恰好合适：4xx（鉴权/参数错）不重试，5xx 与超时重试。若要覆盖超时类异常，显式传 `retry_on`。
2. 节点级 `timeout=`（`TimeoutPolicy`，1.2+）**只对 async 节点生效**，给同步节点设 timeout 会在 `compile()` 阶段直接报错。现有 8 个 Agent 全是 async，安全。
3. 1.2+ 的 `error_handler=`（Saga 补偿）与 `set_node_defaults()`（图级默认）**P2 不启用** —— 留给"真的出现补偿需求"的时候。

#### 4.1.5 并行的收益与代价（要诚实）

- **收益**：`ats` / `impact` / `risk` 三个节点各自一次 LLM 调用，串行下是 3 段延迟，并行后约等于 1 段。live 模式单份可省约 2 × 单节点耗时。**mock 模式零收益**（无网络耗时）。
- **代价**：确定性需要额外守护（固定归并顺序）；`workflow_steps` 的 `latency_ms` 语义从"链路耗时"变成"节点自身耗时"（现状本来也是节点自身耗时，兼容）；调试时日志顺序不再等于执行顺序，需要按 `step_name` 读而不是按时间读。
- **结论**：并行是"有了不亏、没有也能上线"的项，放在 P4，且默认关闭。

#### 4.1.6 人工介入（P5，可选，且有红线）

**先把一个事实说清楚**：Dify 1.17 已**原生支持 Human Input 节点**，暂停/恢复可以全程用 API 驱动（SSE 收到 `human_input_required` → 拿 `form_token` → `GET/POST /form/human_input/{token}` 提交 → 再用 `GET /v1/workflows/run/{id}/events` 重连续跑；表单超时默认 3 天，全局暂停上限 7 天）。所以 **HITL 不再是"必须用 LangGraph"的理由**。

把人工介入放在 LangGraph 侧的依据是另外三条：

1. 裁决要复用 `qc_agent` 的公平性判定与 `workflow_steps` 审计，**业务状态不外移**（反模式 A7）；
2. 裁决结果要落到 `analysis_tasks` 这一条状态机上，而不是分摊在两个系统；
3. Dify 在 Agent app 形态下目前还有"不发 `human_input_required`"的已知未关闭问题（Workflow 形态正常）。

触发条件只能是"**异常信号**"，不能是"分数不好看就让人类放行"：

- `qc` 的 `drop` 比例超阈值（说明模型输出质量异常）；
- 单份成本或 token 超预算上限（成本失控告警）；
- `parse` 置信度异常（规则基线几乎没解析出结构）。

**红线**：`fairness_pass_rate < 1` 时**必须阻断并告警，不允许人工"放行"**。公平性 100% 是 `aaa.md §5` 的验收标准，人工放行等于把验收标准变成可绕过的。

实现要点：触发时节点内 `interrupt({...})` → 状态落 checkpoint → 需要**新增一个裁决端点**（`POST /api/v1/admin/reviews/{task_id}`），因此属于"改接口先改 `docs/api-contract.md`"的范畴；`Command(resume=decision)` 继续执行。管理页当前没有对应 UI，P5 需一并补。

**`interrupt()` 的重放语义（必须写进代码注释）**：resume 时**整个节点函数从头重跑**，不是从 `interrupt()` 那一行继续。因此：

- `interrupt()` **之前**的任何副作用都会**重复执行** → 写库一律 upsert / 幂等键；
- **不要**在 `try/except Exception` 里包 `interrupt()`（它靠抛特殊异常挂起，裸 `except` 会把它吞掉）；
- 同一节点内**只调用一次** `interrupt()`；多个 `interrupt()` 按索引严格匹配、不能条件跳过（会指数级重放）。"校验失败要重问"的官方范式是：节点内只调一次 `interrupt()`，把重问语存进 state，用条件边回环；
- 1.2.12+ 支持 `interrupt(payload, response_schema=PydanticModel)`：把 JSON Schema 交给管理端渲染**类型化审核表单**，并校验 resume 值。这是 P5 人工复核表单的正确实现方式。

#### 4.1.7 `workflow_steps` 的写入时机（保住前端体验）

现状：节点开始前写一行 `running`，结束回填。前端 1.5s 轮询因此能看到"当前步骤正在跑"。
LangGraph 没有 per-node 前置钩子，但**节点函数内部可以在进入时写一行**：

```
node 进入 → upsert(status=running)  → await agent  → upsert(status=done/degraded, tokens/cost/latency)
```

两次都是按 `(task_id, step_name)` 幂等 upsert，重放安全。这样前端看到的节奏与今天完全一致。

#### 4.1.8 依赖与授权边界（已核查）

| 项 | 结论 |
|---|---|
| **用什么** | 只用 `langgraph` + `langgraph-checkpoint-sqlite`（开发）/ `langgraph-checkpoint-postgres`（生产）—— **全部 MIT** |
| **不碰什么** | `langgraph-cli` / `langgraph dev` / `langgraph up` / `langgraph-sdk` / Agent Server（`langgraph-api` 是 **Elastic License 2.0**，生产需 license key + 到 `beacon.langchain.com` 的外联）；也不需要 `langgraph.json` |
| 为什么不碰 Platform | ① 授权从 MIT 变 Elastic-2.0；② 需要 license key 与外联上报（air-gapped 更麻烦）；③ 官方生产路径只推荐 K8s + Helm，非 K8s 有已知缺口要自己补；④ 我们真正需要的只是"图 + checkpoint + `ainvoke`" |
| 新增依赖 | `langgraph`、`langgraph-checkpoint-postgres`（或 `-sqlite`）、`psycopg[binary]`，加 `langchain-core` 等传递依赖（约 10 个包）。**必须钉死版本**（见 §8 R9） |
| 与 `aaa.md` 的关系 | `aaa.md:348` 原文就写着「Agent 编排 = LangGraph」。本方案是把 MVP-lite 主动简化掉的那一项**按"可回退"的方式补回来**，属于回归文档设计，不是推翻设计 |

---

### 4.2 Dify 能力层设计

#### 4.2.1 定位：明确只干三件事，别让它干第四件

**做**：

1. **知识库检索**（替代/增强 `services/rag.py` 的检索段）；
2. **知识库运营**（文档 CRUD、切片与命中调试、rerank 参数试验）；
3. **非主链路运营型 LLM 应用**（报告解读问答、面试问题生成、HR 摘要、知识文档加工）。

**不做**：主链路编排（LangGraph 的活）、评分/证据/规则解析（Python 的活）、Prompt 真源（`prompt_templates` 的活）、业务状态（`analysis_tasks` 的活）。

#### 4.2.2 检索接入的三种调用形态与取舍

| 形态 | 调用方式 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **直接调数据集检索 API** | `POST /v1/datasets/{dataset_id}/retrieve`，请求头用 **dataset 级 API Key**，请求体带 `query` 与 `retrieval_model` | 无应用层、无编排、无对话状态；一次 HTTP 直接拿 segments；调用方仍是 LangGraph 节点 | 要自己维护 `scope → dataset_id` 映射；检索参数写在请求体里 | **主选** |
| 建一个"知识检索"应用再走 Service API | `POST /v1/workflow/run`（blocking/streaming）或 `/chat-messages` | 能在 Dify 控制台调参数、看每次请求日志 | 多一层应用与一次编排；多一份密钥与配额要管；`inputs` 只能传字符串/文件（长 query 要强行序列化） | 备选：当需要非工程同学自己调检索参数时 |
| 反向：Dify 调我们的知识库 | Dify 侧配 External Knowledge → 调我们暴露的 `/retrieval` 端点 | 本地知识库真源完全不动 | Dify 从此依赖我们的可用性，且仍要走 Dify 应用；场景不匹配 | 不必要 |

**必须写进代码审查清单的一点**：Dify 1.17.x 的 `inputs` 在 **schema 层接受任意 JSON**（源码里是 `dict[str, Any]`），并且自 2025-12 起提供 **`JSON Code` 变量类型**（可用 JSON Schema Draft7 强校验）—— **所以"嵌套结构传不进去"已经不是限制**。真正的约束在**变量绑定层**：每个值必须对应 User Input 节点**已声明**的变量，且**没有顶层数组类型**。

也就是说，把 `AgentContext` 塞进 Dify 技术上可行，但代价是：为每个 Agent 声明一堆输入变量（维护成本随节点数线性增长）、把 `parsed_resume` 落进 Dify 的运行日志（隐私面扩大，且日志默认不清理）、并给每次调用增加一次跨进程往返。

**结论：检索只传一个 query 字符串之所以仍然是最优形态，不是因为"传不了"，而是因为"不该传"。** 这也是本方案选择**检索走 Dify、编排留在 LangGraph** 的关键理由。

#### 4.2.3 检索参数映射表

| 本地现状 | Dify 对应 | 说明 |
|---|---|---|
| `rag_top_k=5` | `retrieval_model.top_k` | 保持一致，A/B 才有可比性 |
| 混合打分 `0.6 × 向量 + 0.4 × 词面` | 混合检索 + 语义/关键词权重 | 把权重平移过去（0.6 / 0.4） |
| `min_score=0.05` | `score_threshold` | **两者分数尺度不同，不要照抄 0.05**，要重新标定 |
| `scope ∈ role / industry / resume_template / compliance` | dataset 维度**或** metadata 过滤 | 建议 4 个 scope → 4 个 dataset：最省事，权限也干净 |
| 无 rerank | rerank 模型（可选） | 相对现状的**净增益**，也是 A/B 要量化的主变量 |
| 本地哈希向量（1536 维、确定性） | Dify 侧的 embedding 模型（OpenAI 兼容 / 本地 Ollama / 其他） | `embeddings.py:1-12` 自述这是短板 —— 此处是**主要收益** |
| 命中结构 `{chunk_id, doc_id, title, scope, score, text}` | 映射层（见 §4.5） | 契约结构不变 |
| 无缓存 | （可选）Dify 侧缓存 / 本地结果缓存 | 同一 JD 反复检索同一 query，加一层缓存能省调用 |

> **一个反直觉但极其关键的坑（官方文档明确）**：Dify 的 **TopK 与 Score Threshold 只在 Rerank 阶段生效** —— **不启用 rerank 模型时，这两个参数形同虚设**（默认 TopK=3、阈值=0.5）。因此 §7 P4 的 A/B 必须设三组：① 现状（本地混合检索）；② Dify 不启用 rerank；③ Dify 启用 rerank。否则很容易把"参数根本没生效"误判成"Dify 检索没提升"。

#### 4.2.4 Prompt 治理：真源不动，Dify 只当试验场

- **真源**：`services/prompts.py` 的 `DEFAULTS` + `prompt_templates` 表（契约 §6.3）。
- **Dify 的角色**：Prompt IDE 用来调参 —— 在 Dify 里搭一个与某个 Agent 同构的单节点应用，粘贴当前 prompt，调完把定稿**经既有的 `POST /api/v1/admin/prompts` 写回**（版本号自增，工作流下次执行即生效）。
- **为什么不让 Dify 当 prompt 真源**：`POST /admin/prompts` 是已冻结契约，真源搬到 Dify 后这个接口会变成"改了不生效"的假接口；而且两版 prompt 的效果数据会分散在两个系统里，A/B 无法闭环。
- **如果确实想让 Dify 承担编辑职责**：必须保证"改动可版本化地回流"（DSL 导出 + 人工/CI 导入到 `prompt_templates`），且运行期**唯一读取源仍是 `prompt_templates`**。

#### 4.2.5 运营型应用（B 档就能拿到的价值）

| 应用 | 输入 | 输出 | 与主链路的关系 |
|---|---|---|---|
| 报告解读问答 | 已生成报告的结构化字段（**不含简历原文**）+ 用户问题 | 自然语言回答 | 只读已有报告；挂了不影响诊断 |
| 面试问题生成 | 报告 findings 摘要 + JD 关键词 | 面试问题清单 | 新增能力，不替代 `suggestion_agent` |
| HR 批量摘要 | 多份报告的分数与 top findings（**不含原文**） | 候选人横向对比文字摘要 | 只做文字汇总；**榜单仍由 `hr.py` 计算**（契约 §6.5 不变） |
| 知识文档加工 | 原始资料 | 结构化知识文档草稿 | 产物经 `POST /knowledge/docs` 入库（人工审核后） |

这些应用全部**只读主链路已落库的结果**，因此：失败可降级、不需要 checkpoint、不参与"单份报告 ≤ 0.5 元"的成本红线核算（用量单独统计）。

#### 4.2.6 落地检查表（隐私与运维，逐项打勾）

- [ ] 自托管、内网部署，不使用 Dify Cloud
- [ ] 主链路检索请求体里**只有 query**（code review 检查项）
- [ ] 运营应用关闭/收紧日志记录；若该版本无法关闭，则只传脱敏内容
- [ ] Dify 的 Postgres / Redis / 向量库纳入同一份备份、加密与访问控制
- [ ] Dify 管理员账号与业务后台账号分离，最小权限
- [ ] 登记"哪个业务模块用了哪个 Dify 应用 + 哪个密钥"，与 `knowledge_sync_state` 一起可审计
- [ ] Dify 不可用时 `/api/v1/health` 与主链路任务仍可用（降级矩阵 §4.4）
- [ ] **出网白名单**：Dify 的 `ssrf_proxy` **默认拒绝私网**。若 Dify 需要调用内网的 FastAPI / LangGraph，必须配 `SSRF_PROXY_ALLOW_PRIVATE_IPS`，否则 HTTP Request 节点直接连不通（社区已知坑）
- [ ] **向量库选外部**：1.17.1 的内置 Weaviate 从 1.27.0 跳到 1.39.2（跨 12 个 minor），官方明确警告"跳过 minor 不受支持、可能**静默且永久地**损坏向量检索" → 用 pgvector 等外部向量库规避
- [ ] **日志生命周期**：Dify 日志默认**不清理**（`WORKFLOW_LOG_CLEANUP_ENABLED=false`），`workflow_node_executions` 会持续膨胀；要么开启清理，要么把节点执行明细转到对象存储（`WORKFLOW_NODE_EXECUTION_STORAGE=hybrid`）
- [ ] **可观测采样率**：OTel 默认 `ENABLE_OTEL=false`；一旦开启，默认采样率只有 **0.1**。追踪后端是在**每个 app 的 Monitoring 页面**粘贴凭据（不是环境变量）
- [ ] **限流**：自托管默认 `APP_DEFAULT_ACTIVE_REQUESTS=0` / `APP_MAX_ACTIVE_REQUESTS=0`（都不限），上生产前要自己压测出限流点
- [ ] **不用 `inputs` 传简历原文**（§4.2.2：技术上能传，业务上不该传）
- [ ] **只使用 `/v1/*` Service API**，绝不用 `/console/api/*`（反模式 A8）

#### 4.2.7 版本、部署与授权事实（已核查）

| 事实 | 值 / 出处 | 对方案的影响 |
|---|---|---|
| Dify 版本线 | 调研时点最新稳定版 **v1.17.1**（发布节奏较快，落地前请再确认一次最新 tag）；v1.16 起引入 Agent V2 与双沙箱，v1.9 起引入 Knowledge Pipeline | 自托管**锁版本**，不跟 `main`；升级按"有风险的操作"对待 |
| 官方最低硬件 | **CPU ≥ 2 核、RAM ≥ 4 GiB**；**Docker Compose ≥ 2.24.0**；Windows 需 WSL2，且**源码与数据要放在 Linux 文件系统里**（不要挂 Windows 盘） | 4 GiB 只是"能起来"，跑真实检索要更多（见 §9）。本项目在 E:（Windows FS）→ Dify 数据卷必须放 WSL2 的 ext4 |
| 容器数量 | 1.17.x 实际为：`api` / `api_websocket` / `worker` / `worker_beat` / `web` / `db` / `redis` / `sandbox` / `local_sandbox` / `plugin_daemon` / `agent_backend` / `ssrf_proxy` / `agent_ssrf_proxy` / `nginx` + 向量库（启 milvus profile 再多 3 个） | 与现有前后端抢资源 → 必须做"最小化 compose"（§9） |
| 知识库检索 | `POST /datasets/{dataset_id}/retrieve`，**生产检索与测试检索是同一端点**；支持 **dataset 级 scoped API key**（只能访问被授权的 dataset，越权返回 403 `forbidden`） | 支撑 §4.2.2 的主选形态；也意味着密钥可以按 scope 收紧 |
| 检索能力 | 向量 / 全文 / **混合检索**（权重可调）、**支持 rerank 模型**、支持 metadata 与标签过滤、支持自定义 embedding（OpenAI 兼容 / 本地 Ollama 等） | 直接补上 `services/embeddings.py:1-12` 自述的短板 |
| 知识库写入 | Knowledge API 提供 dataset / document / chunk / metadata / tag 的完整 CRUD；Knowledge Pipeline（v1.9+）把解析→清洗→分块→嵌入做成可视化流水线 | 支撑 §4.5 的"本地真源 → Dify 副本"同步器 |
| Service API | 每个应用自带 REST API，`Authorization: Bearer <key>`，base URL `/v1`；**key 只能放后端** | 运营型应用按此接入 |
| 流式 | `response_mode = blocking \| streaming`；SSE 每帧 `data: <json>`，**开头及约每 10 秒有裸 `event: ping` 心跳帧**（无 `data:`），解析时必须跳过；官方明确 **blocking 模式下长请求会被代理切断** | 若将来要代理 Dify 的流式输出，必须处理 ping 帧；主链路检索用 blocking 即可（短请求） |
| Dify 反向调我们 | **External Knowledge API**：我们实现 `POST {endpoint}/retrieval`，请求体 `{knowledge_id, query, retrieval_setting:{top_k, score_threshold}, metadata_condition?}`，响应 `{records:[{content, score, title, metadata}]}`；`metadata` 必须是对象、**不能是 null** | 备选形态（§4.2.2 第三种）；当前不需要，但它是"Dify 用我们知识库"的官方通道 |
| 与 LangChain/LangGraph 的官方集成 | **没有官方集成**；PyPI 上的 `langchain-dify` 是个人包（元数据为空） | **直接用 `httpx` 调 Dify API，不引第三方包装包** |
| 授权 | Dify 采用**修改版 Apache-2.0 + 附加条款**。已确认的两条：① **未经书面授权不得做多租户运营**（1 tenant = 1 workspace）；② LOGO/版权保留条款**仅在前端（`web`）场景生效，纯 API 调用不受约束**；而"**作为其他应用的后端服务**"这种商用形态是**被明确允许**的 | 本方案是自托管 + 单一租户内部使用 → 风险低。**对外提供多租户 SaaS 之前必须取得授权**（这是硬条款，不是建议） |

> **一条必须写下来的诚实说明**：调研**没有找到任何 Dify 官方文档把"Dify + LangGraph 混合架构"列为受支持的模式**，也没有官方集成页。本方案的"职责切分"是工程判断，不是官方推荐做法；其中"检索走 Dify、编排留 LangGraph"这一条有官方 API 支撑（知识检索 API 是官方接口），但"两者如何组合"没有任何一方背书。落地时请把它当作**自建集成**对待：自己的超时、自己的幂等、自己的降级。

> **同时纠正本方案早期版本里的三个过时前提**（已在上文修正）：① Dify 1.17.x 的 `inputs` **已支持任意 JSON**（含 `JSON Code` 类型），"只能传字符串"是 2025-09 之前的限制；② Dify **已原生支持 HITL**（Human Input 节点 + 表单 API + 断线重连），且支持有界循环/迭代/并行/子流程（Loop ≤100、并行 ≤10、单路径 ≤50 节点、子流程深度 ≤5）；③ Dify **仍然没有**任何官方 API 能读写线上应用配置，唯一程序化路径是 DSL + `difyctl`，且**导出的是 Current Draft、导入写到 Draft、必须在界面里手动 Publish 才生效** —— ③ 正是"Prompt 真源必须留在 `prompt_templates`"的硬理由（§4.2.4）。



### 4.3 FastAPI 业务层：保留什么、改什么

| 项 | 处置 |
|---|---|
| `api/*` 全部路由、`deps.py`、`serializers.py`、`errors.py`、`schemas.py` | **零改动** |
| `workflow/__init__.py` | 保持只导出 `launch_workflow`（`api/tasks.py` 只 import 它），签名不变 |
| `workflow/engine.py` | 保留为 legacy 实现；新实现放 `workflow/graph.py`，由 `WORKFLOW_ENGINE` 开关分派 |
| `main.py` lifespan | 新增"僵尸任务回收"钩子（幂等、可关闭） |
| `services/rag.py` | 抽 `Retriever` 接口，`/knowledge/search` 的响应结构不变 |
| 新增端点 | 仅 P5 的人工裁决需要；必须同步更新 `docs/api-contract.md` |

### 4.4 配置开关与降级矩阵

```ini
# ---- 编排 ----
WORKFLOW_ENGINE=legacy|langgraph        # 默认 legacy（灰度期），全绿后改 langgraph
WORKFLOW_PARALLEL=false                 # 并行 ats/impact/risk
WORKFLOW_REWORK_MAX=0                   # 质检回环次数上限；0=关闭（保持现状）
LANGGRAPH_CHECKPOINT=off|sqlite|postgres
LANGGRAPH_CHECKPOINT_URL=               # 留空则复用 DATABASE_URL
ZOMBIE_TASK_TIMEOUT_S=300               # 僵尸任务回收阈值

# ---- 能力层 ----
RAG_BACKEND=local|dify                  # 默认 local（现状）
DIFY_ENABLED=false                      # 总开关；false 时 services/dify.py 完全不加载
DIFY_BASE_URL=http://127.0.0.1:5001
DIFY_DATASET_API_KEY=                   # 知识库检索用（dataset 级 key）
DIFY_APP_API_KEY=                       # 运营型应用（service 级 key），与上者分离
DIFY_KNOWLEDGE_DATASET_IDS=             # scope → dataset_id 的映射，逗号分隔
DIFY_TIMEOUT_S=15
DIFY_SYNC_MODE=off|manual|startup       # 知识库同步策略
```

**降级矩阵**（每一行都要有自动化验证）：

| 故障 | 行为 | 验收点 |
|---|---|---|
| `DIFY_ENABLED=false` | `RAG_BACKEND` 强制回落 `local`，其余完全不变 | 现有 174 项断言全绿（零行为差异） |
| Dify 检索超时/5xx/熔断 | 单次任务内回落到本地混合检索，报告照出，`workflow_steps.notes` 记录 `dify_fallback` | 「知识库引用非空（RAG 生效）」仍通过 |
| Dify 整站不可用 | 同上；若要把 `rag_backend` 暴露到 `/api/v1/health` 作为可观测字段，属于**新增响应字段** → 按项目约定必须先在 `docs/api-contract.md §1` 里立项（`verify.py` 的"health 字段完整"是包含式检查，新增字段本身不会挂，但契约文档必须同步） | 健康检查仍 200 |
| LangGraph 不可用 | `WORKFLOW_ENGINE=legacy` 一键回退（图实现与 legacy 共享同一套 `services/*`） | 回退后 `verify.py` 全绿 |
| checkpoint 库不可用 | `LANGGRAPH_CHECKPOINT=off` 时退化为"无断点续跑"，功能可用、崩溃后任务需重跑 | 明确记录为已知损失 |
| 无 LLM Key（mock） | 现行为不变；Dify 的知识库检索仍可用（检索不需要 LLM Key，但需要 embedding 模型可用） | 「无需 API Key 跑通全链路」 |

### 4.5 数据与知识库同步（唯一双写点，按"本地真源"设计）

**决策：`KnowledgeDoc` / `KnowledgeChunk` 仍是知识库真源，Dify 是"检索副本"。**

四个理由：

1. 契约 §6.2 的 `POST /knowledge/docs` 必须返回本地 `KnowledgeDoc`（含本地 id），`GET /knowledge/docs` 要能列出 —— 真源在 Dify 就得把本地表降级成缓存，删除/归属/审计全部要重做；
2. `DIFY_ENABLED=false` 与 mock 模式必须仍然可用（红线 3）；
3. `services/seed.py` 的 16 篇种子文档、`scope ∈ role|industry|resume_template|compliance` 的过滤语义必须继续生效；
4. 用户隐私删除（`DELETE /resumes/{id}`）不该牵连知识库；但反过来，一旦 Dify 变成真源，**删除知识库文档就变成跨系统事务**，得不偿失。

同步方向：**业务库 → Dify（单向）**。

```
KnowledgeDoc 变更（新建/编辑/删除）
   → knowledge_sync_state 表记录 (doc_id, content_hash, dify_document_id, status, synced_at, error)
   → 后台同步器：create/update by document → 切片 → 写 embedding
   → segment.metadata = { local_doc_id, local_chunk_id, scope, role_family, doc_type }
   → 检索命中后按 metadata 映射回本地 chunk_id/doc_id
```

几个必须写下来的细节：

| 细节 | 处理 |
|---|---|
| 契约字段 `chunk_id` / `doc_id` 的来源 | 从 Dify segment metadata 里取本地 id 映射回来；**没有映射的（Dify 侧新增的文档）标记 `source="dify"` 并返回 Dify id 作为兜底**，不让响应字段缺失 |
| `score` 语义 | Dify 返回相似度分数，区间与本地 0–1 混合分不同 → 映射时**归一化并记录 `scorer` 字段**；不要假装两种分数可直接比较 |
| `scope` 过滤 | Dify 支持 metadata 过滤时用它；否则按 `scope` 建多个 dataset（4 个 scope = 4 个 dataset，检索时按需选） |
| 切片策略 | 本地 `split_chunks`（target 420 / overlap 60）与 Dify 的切片器不一致 → **以 Dify 侧为准并接受差异**，但保留本地切片作为 fallback 通道的输入；两边切片不同会导致同一 query 命中不同文本，这是可接受的（都属于"检索质量"范畴，不属于契约） |
| 同步失败 | 不阻塞主链路；`knowledge_sync_state.status=failed` + `error`，管理后台可见（可复用 `/admin/metrics` 新增一个只读字段） |
| 全量重建 | `scripts/sync_knowledge.py --force`（新增），与 `scripts/seed_knowledge.py --force` 配套 |

**这张表是本方案里唯一新增的表**（`models.py` 属"加不改"）：

```
knowledge_sync_state(id, doc_id, content_hash, dify_dataset_id, dify_document_id,
                     status, attempts, error, synced_at, created_at, updated_at)
```

### 4.6 隐私与合规（这一节是上 Dify 的**前置条件**，不是可选项）

Dify 作为 LLMOps 平台，会把它自己应用的输入输出落到**它自己的数据库**（日志/追踪）。简历原文含手机号、邮箱、住址。因此：

| 规则 | 具体要求 |
|---|---|
| 主链路**不传简历原文**给 Dify | 检索只传 query（岗位名 + 技能 + 关键词，`rag.build_query` 的现有产物），**不传 `resume_text`** |
| 主链路**不把 8 个 Agent 做成 Dify 应用** | 根本原因见反模式 A1/A4：一旦做成应用，`parsed_resume` 会作为 inputs 落进 Dify 库 |
| 运营型应用若必须传简历内容 | 传**脱敏副本**（手机号/邮箱/身份证/住址正则掩码），并在 Dify 侧关闭该应用的日志记录（若版本不支持关闭，则不传） |
| 删除与导出 | `aaa.md §4`「用户可删除数据」必须覆盖 Dify：`DELETE /resumes/{id}` 若触达过 Dify 应用日志，需要提供 Dify 侧的清理路径（最省事的做法就是"不传"，见第一条） |
| 数据驻留 | Dify 自托管在**内网**，与其 Postgres 纳入同一份隐私管理（备份、加密、访问控制）；不用 Dify Cloud |
| 审计 | 谁改了知识库/prompt：Dify 侧的改动必须回流到本地（prompt 经 `POST /admin/prompts`，知识库经 `knowledge_sync_state`），保证本地仍能回答"线上到底用的哪一版" |

---

## 5. 关键代码骨架

> 骨架只演示"接线方式"，不复制既有 Agent 内部逻辑。所有 `agents/*`、`analyze/evidence/parse_rules/cost` 保持零改动。

### 5.1 把现有 Agent 包成图节点（`workflow/nodes.py`）

```python
CONTRACT_STEPS = [c.name for c in AGENT_PIPELINE]        # 8 个，顺序即契约

def _ctx_from_state(state: WorkflowState) -> AgentContext:
    ctx = AgentContext(
        task_id=state["task_id"],
        resume_text=state.get("resume_text", ""),
        jd_text=state.get("jd_text", ""),
        parsed_resume=_safe_resume(state.get("parsed_resume")),
        parsed_jd=_safe_jd(state.get("parsed_jd")),
        role_family=state.get("role_family", "general"),
        knowledge_hits=list(state.get("knowledge_hits") or []),
        prompt_overrides=dict(state.get("prompt_overrides") or {}),
    )
    # 回填累积字段：语义与 legacy 完全一致（整体赋值，不是 append）
    ctx.findings = list(state.get("findings") or [])
    ctx.missing_keywords = list(state.get("missing_keywords") or [])
    ctx.covered_keywords = list(state.get("covered_keywords") or [])
    ctx.action_items = list(state.get("action_items") or [])
    ctx.knowledge_refs = list(state.get("knowledge_refs") or [])
    ctx.scores = dict(state.get("scores") or {})
    ctx.summary = state.get("summary", "")
    ctx.qc_report = dict(state.get("qc_report") or {})
    ctx.outputs = dict(state.get("outputs") or {})
    return ctx


def _patch(ctx: AgentContext) -> dict:
    """节点返回值：只有这些键参与状态合并（其余键保持不变）。"""
    return {
        "parsed_resume": ctx.parsed_resume.model_dump(),
        "parsed_jd": ctx.parsed_jd.model_dump(),
        "role_family": ctx.role_family,
        "findings": ctx.findings,          # 完整列表 = 保留"替换"语义
        "missing_keywords": ctx.missing_keywords,
        "covered_keywords": ctx.covered_keywords,
        "action_items": ctx.action_items,
        "knowledge_refs": ctx.knowledge_refs,
        "scores": ctx.scores,
        "summary": ctx.summary,
        "qc_report": ctx.qc_report,
        "outputs": ctx.outputs,
    }


def make_agent_node(agent_cls) -> Callable:
    async def node(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
        agent = agent_cls()
        llm = runtime.context.llm                      # 1.x：context 对象，不再是 dict
        ctx = _ctx_from_state(state)

        # 1) 开始：写 running 行（保住前端"当前步骤正在跑"的观感）
        record_step(state["task_id"], agent.name, status="running",
                    input_digest=_input_digest(agent.name, ctx))

        started = time.perf_counter()
        try:
            out = await agent.safe_run(ctx, llm)      # 现状语义：非 critical 异常 → degraded
        except Exception as exc:                       # 只有 critical（parse）会走到这里
            record_step(state["task_id"], agent.name, status="failed",
                        notes=f"{type(exc).__name__}: {str(exc)[:300]}")
            raise WorkflowAbort(f"{agent.title} 执行失败：{str(exc)[:200]}") from exc

        # 2) 结束：回填 tokens/cost/latency（F5）
        record_step(
            state["task_id"], agent.name,
            status="degraded" if out.status == "degraded" else "done",
            tokens=out.tokens, cost=out.cost,
            latency_ms=out.latency_ms or int((time.perf_counter() - started) * 1000),
            notes=out.notes, output_digest=_truncate(out.data),
        )

        patch = _patch(ctx)
        patch["steps"] = [{"step_name": agent.name, "contract_step": True,
                           "status": "degraded" if out.status == "degraded" else "done",
                           "tokens": out.tokens, "cost": out.cost,
                           "latency_ms": out.latency_ms, "notes": out.notes}]
        return patch

    node.__name__ = f"node_{agent_cls.name}"
    return node
```

### 5.2 图（`workflow/graph.py`）

```python
# backend/app/workflow/graph.py（新增）
# 只依赖 MIT 的 OSS 库；不使用 langgraph-cli / Agent Server（Elastic-2.0）
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from langgraph.runtime import Runtime


def build_graph(*, parallel: bool = False, checkpointer=None):
    # 1.x：StateGraph(state_schema, context_schema=...)
    g = StateGraph(WorkflowState, context_schema=GraphContext)

    g.add_node("setup", setup_node)
    g.add_node("retrieve", retrieve_node, retry_policy=RetryPolicy(max_attempts=3))

    for agent_cls in AGENT_PIPELINE:                      # 8 个契约节点
        g.add_node(agent_cls.name, make_agent_node(agent_cls))

    g.add_node("merge", merge_node)                       # 仅并行版生效
    g.add_node("finalize", finalize_node)                 # = F1：qc 后重算分数，不写 step 行
    g.add_node("persist", persist_node)                   # 落报告 + task.done + steps 汇总，幂等
    if settings.workflow_rework_max > 0:
        g.add_node("rework", rework_node)

    g.add_edge(START, "setup")
    g.add_edge("setup", "retrieve")
    g.add_edge("retrieve", "parse")
    g.add_edge("parse", "jd_match")

    if parallel:
        for name in ("ats", "impact", "risk"):
            g.add_edge("jd_match", name)
            g.add_edge(name, "merge")
        g.add_edge("merge", "suggestion")
    else:
        g.add_edge("jd_match", "ats"); g.add_edge("ats", "impact")
        g.add_edge("impact", "risk");  g.add_edge("risk", "suggestion")

    g.add_edge("suggestion", "report")
    g.add_edge("report", "qc")
    g.add_conditional_edges("qc", route_after_qc,
                            {"finalize": "finalize", "rework": "rework"})
    if settings.workflow_rework_max > 0:
        g.add_edge("rework", "suggestion")
    g.add_edge("finalize", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)


def finalize_node(state: WorkflowState) -> dict:
    """F1：质检会剔除/降级发现项，必须在这里重新定稿分数。"""
    ctx = _ctx_from_state(state)
    ctx.scores = analyze.compute_scores(ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
    return {"scores": ctx.scores}
```

进入点（放在 `engine.py`，签名与现状完全一致）：

```python
async def run_graph(task_id: str) -> str | None:
    # checkpointer 按任务构建，避开「全局 async checkpointer + asyncio.run」的事件循环绑定坑
    async with make_checkpointer() as cp:
        graph = build_graph(parallel=settings.workflow_parallel, checkpointer=cp)
        final = await graph.ainvoke(
            {"task_id": task_id},
            # Python 3.10 + async：必须显式把 config 传进 ainvoke（官方已知限制）
            config={"configurable": {"thread_id": task_id}},
            context=GraphContext(llm=get_llm(), retriever=get_retriever()),
            durability="async",          # 崩溃可续跑；要求更高就用 "sync"（更慢）
        )
    return final.get("report_id")
```

### 5.3 `engine.py` 的开关式改造（最小 diff）

```python
def launch_workflow(task_id: str) -> None:
    try:
        if settings.workflow_engine == "langgraph":
            from .graph import run_graph
            asyncio.run(run_graph(task_id))
        else:
            asyncio.run(run_workflow(task_id))       # 现状实现，原样保留
    except Exception:                                # noqa: BLE001
        traceback.print_exc()
        _mark_failed(task_id, "工作流异常终止")        # 新增：避免任务卡在 running
```

`_safe_resume` / `_safe_jd` / `_persist_report` / `_input_digest` / `_truncate` **原样复用**（提升为 `workflow/common.py` 供两版共享，或直接 import）。

### 5.4 幂等步骤写入（`workflow/steps.py`）

```python
def record_step(task_id: str, step_name: str, **fields) -> None:
    """按 (task_id, step_name) upsert；节点重放不会产生第二行。"""
    with session_scope() as db:
        row = db.execute(
            select(WorkflowStep).where(
                WorkflowStep.task_id == task_id, WorkflowStep.step_name == step_name
            )
        ).scalar_one_or_none()
        if row is None:
            row = WorkflowStep(task_id=task_id, step_name=step_name)
            db.add(row)
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)
        _advance_progress(db, task_id)


def _advance_progress(db, task_id: str) -> None:
    """progress = 已完成契约步（去重） / 8 —— 重放安全，最终必为 1.0。"""
    rows = db.execute(select(WorkflowStep).where(WorkflowStep.task_id == task_id)).scalars().all()
    done = {r.step_name for r in rows if r.status in ("done", "degraded", "failed")}
    contract_done = [n for n in CONTRACT_STEPS if n in done]
    task = db.get(AnalysisTask, task_id)
    if task:
        task.progress = round(len(contract_done) / len(CONTRACT_STEPS), 4)
        if contract_done:
            task.current_step = contract_done[-1]
```

配套 DDL（唯一索引，**加不改**）：

```python
__table_args__ = (UniqueConstraint("task_id", "step_name", name="uq_workflow_step_task_name"),)
```

### 5.5 检索抽象（`services/rag.py`）

```python
class Retriever(Protocol):
    def search(self, db, query: str, *, top_k: int, scope: str | None) -> list[dict]: ...

class LocalHybridRetriever:      # 现状实现原样搬入，行为不变（0.6 向量 + 0.4 词面）
    ...

class DifyRetriever:             # 新增：HTTP 调 Dify 数据集检索
    def __init__(self, client: DifyClient, dataset_map: dict[str, str]) -> None: ...
    def search(self, db, query, *, top_k, scope):
        raw = self.client.retrieve(self.dataset_map[scope or "role"], query, top_k=top_k)
        return [self._to_hit(r) for r in raw]      # 映射回契约字段，见 §4.5

class FallbackRetriever:         # 组合：Dify 失败 → 本地，并记录降级原因
    ...

def get_retriever() -> Retriever:
    if settings.dify_enabled and settings.rag_backend == "dify":
        return FallbackRetriever(DifyRetriever(...), LocalHybridRetriever())
    return LocalHybridRetriever()
```

`api/knowledge.py::/knowledge/search` 与 `engine.py` 的调用点**只换成 `get_retriever().search(...)`**，响应结构不动。

---

## 6. 契约不变性清单（改造后必须逐条复验）

### 6.1 `verify.py` 中与本次改造强相关的断言（按风险排序）

| 断言（原文） | 风险来自 | 保护手段 |
|---|---|---|
| 总步数为 8 / 步骤数为 8 / **步骤顺序与契约一致** | 图节点被当成步骤写进了 `workflow_steps` | `contract_step=False` 的节点绝不写表；节点名严格取自 `AGENT_PIPELINE[].name` |
| **progress 达到 1.0** | 并行/回环导致进度算法失真 | 进度由"已完成契约步去重 ÷ 8"推导；`persist` 里兜底置 1.0 |
| 所有步骤均完成或降级 | 重试策略改到了 LLM 层，`degraded` 丢失 | LLM 重试留在 `services/llm.py`；节点级 `RetryPolicy` 只管 Dify |
| 发现项 ≥ 5 条 / **发现项按 red→yellow→green 排序** | `findings` 加了 reducer 或并行归并顺序不稳 | `findings` 不加 reducer；归并顺序固定 ats→impact→risk |
| 每条红色风险都有可定位原文引用 | `evidence.span` 被外移 | `evidence.py` 零改动 |
| 同一简历两次诊断分数一致（可回归） | 分数被 LLM/Dify 接管 | `compute_scores` 零改动；`finalize` 节点位置与 legacy 一致（qc 之后） |
| **知识库引用非空（RAG 生效）** | 切到 Dify 检索后映射失败/为空 | `FallbackRetriever` 保证非空；`metadata.local_chunk_id` 映射 |
| 关键词类发现项的引文是关键词本身 | `jd_match` 的过滤逻辑被改写 | Agent 零改动 |
| 该样本触发时间线空窗检测 / 报告包含风险类别发现项 | 发现项被并行打乱或 QC 顺序变化 | 串行版默认；并行版固定归并 |
| 分类成本 ≤ 0.5 元 / 公平性通过率 100% | 成本与公平性被搬到外部平台 | 成本仍由 `workflow_steps` 核算；公平性仍在 `risk_agent` + `qc_agent` |
| 无需 API Key 跑通全链路 | Dify 成为必经路径 | `DIFY_ENABLED=false` 是默认值；`RAG_BACKEND=local` 是默认值 |
| 删除简历返回 204 / 删除后 404 | Dify 侧残留副本 | 主链路不把简历原文写入 Dify（§4.6） |
| **所有步骤均完成或降级** | 节点重放导致缺行/多行；或某步被静默跳过 | 唯一索引 + upsert（`serialize_task` 会按 `STEP_ORDER` 补 `pending` 行，所以"缺行"不会表现为"只有 7 行"，而是表现为这条断言失败 —— 这是最容易误判的一条） |
| **步骤成本明细覆盖 8 步**（`/admin/metrics`） | `admin.metrics` 按 `step_name` GROUP BY 后按**第三处硬编码顺序**排序（`api/admin.py:56`）；步名一变就落空 | 步名严格取自 `AGENT_PIPELINE[].name`；三处顺序（`agents/__init__.py:13-22`、`serializers.py:112`、`admin.py:56`）必须同步 |
| 单份成本 ≤ 0.5 元 | 重复写 `workflow_steps` 会让 `SUM(tokens)`/`SUM(cost)` 虚高 | 同上（唯一索引 + upsert） |
| **同一简历两次诊断分数一致**（注意其边界） | `analyze.timeline_gaps` / `gap_findings` 用 `datetime.now()` 当"至今"（`analyze.py:415-418, 560-563`） | 现状只在**同一天内**成立；这是既有边界，不是本次改造引入的。回归脚本要在同一天跑，或接受这条断言跨日可能不稳定 |
| 任务执行成功 / progress 达到 1.0 | 工作流被挪到独立 worker/队列后，TestClient 不再同步等待 | **P2/P3 保持 `BackgroundTasks`**（见 §4.1.3b / R18）；要上队列必须同步改造 `verify.py` |

### 6.2 前端硬编码（改了就是白屏）

| 位置 | 内容 |
|---|---|
| `frontend/components/step-timeline.tsx:6` | `STEP_ORDER`：8 个步名与顺序 |
| `frontend/components/step-timeline.tsx:17` | `STEP_LABELS`：步名 → 中文标签映射 |
| `frontend/app/analyze/[taskId]/page.tsx:16` | `POLL_INTERVAL_MS = 1500` |
| `frontend/lib/types.ts:285-289` | `StepName` 联合类型 |
| `frontend/components/finding-card.tsx:118-130` | `evidence.{source,field,quote,span}` 渲染 |
| `frontend/components/report-view.tsx:238-253` | `report.knowledge_refs[].title` |

结论：本次改造对这 6 处零改动。**任何需要改它们的设计都应当被否决或推迟到"契约 v2"。**

除前端外，后端还有**三处硬编码的同一份步序**，改动时必须三处同改：

| 位置 | 内容 |
|---|---|
| `backend/app/agents/__init__.py:13-22` | `AGENT_PIPELINE`（执行顺序） |
| `backend/app/serializers.py:112` | `STEP_ORDER`（`total_steps` 与时间线补齐的来源） |
| `backend/app/api/admin.py:56` | `order = [...]`（看板 `step_breakdown` 排序） |

### 6.3 一个必须写进代码审查清单的点

`finalize` / `persist` / `merge` / `setup` / `retrieve` 这些节点**不得**调用 `record_step`。建议把 `record_step` 的调用点集中在 `make_agent_node` 内部一处，并用一个断言把"图节点名集合"与"契约步名集合"的关系固化下来：

```python
assert {n for n in graph_nodes if n in CONTRACT_STEPS} == set(CONTRACT_STEPS)
assert len(CONTRACT_STEPS) == 8
```

---

## 7. 分阶段路线图

> 每一阶段结束时都必须满足：**可独立部署 + 对外行为不变 + `verify.py` 全绿**。任何一阶段做不到这三点，就回退该阶段。

| 阶段 | 目标 | 关键改动文件 | 验收 | 回退 | 预估 |
|---|---|---|---|---|---|
| **P0 基线锁定** | 把"行为不变"变成可自动比对 | 新增 `backend/tests/`：`compute_scores` 金标快照、`locate_quote` 用例、`parse_rules` 样本基线、`rag.search` 排序快照；记录一次 live 模式 8 步 tokens/cost 作为对账基线 | `pytest` 全绿 + `verify.py` 174/174 + 基线快照文件入库 | 无（纯新增） | 2–3 天 |
| **P1 接口抽象** | 让"换实现"不碰调用方 | `services/rag.py` 抽 `Retriever`；`services/llm.py` 抽 `LLMProvider`（可选）；`services/dify.py` 客户端空实现 | 同上 + 开关默认值 = 现状 | 删除新文件即可 | 2 天 |
| **P2 LangGraph 串行接管** | 主链路跑在图里，行为等价 | `workflow/state.py`、`nodes.py`、`graph.py`、`steps.py`、`common.py`；`engine.py` 加开关；`models.py` 加唯一索引 | `WORKFLOW_ENGINE=langgraph` 下 `verify.py` 全绿；同一简历两次诊断分数一致 | `WORKFLOW_ENGINE=legacy` | 5–7 天 |
| **P3 checkpoint + 僵尸回收 + 清理与并发锁** | 断点续跑可用，且不留下运维债 | `LANGGRAPH_CHECKPOINT=sqlite`；`main.py` lifespan 回收钩子；**单 thread 执行锁**（§4.1.3b）；`scripts/prune_checkpoints.py` + 定时任务 | 手动 kill 进程后重启，任务从断点继续并产出报告，`workflow_steps` 无重复行；同一 `task_id` 并发调用被拒绝；checkpoint 行数不单调增长 | `WORKFLOW_ENGINE=legacy` 或 `CHECKPOINT=off` | 4–5 天 |
| **P4 Dify 知识检索 + 并行 + 回环** | 拿到 Dify 的真实收益 | `services/dify.py` 实装；`DifyRetriever` / `FallbackRetriever`；`scripts/sync_knowledge.py`；`knowledge_sync_state` 表；`WORKFLOW_PARALLEL=true`；`WORKFLOW_REWORK_MAX=1` 试点 | 检索质量 A/B（同一批 query，人工评估 top-5 命中率）；`知识库引用非空` 在 Dify 挂掉时仍通过 | `RAG_BACKEND=local` / `PARALLEL=false` / `REWORK_MAX=0` | 5–8 天（含 Dify 部署） |
| **P5 运营型应用 + 可观测 + 人工复核** | 把 Dify 的价值放到非主链路上 | Dify 侧建"报告解读问答""面试问题生成""HR 批量摘要"应用；FastAPI 代理端点（同步更新契约文档）；可选 Langfuse；人工复核端点与后台页 | 新端点有契约条目 + `verify.py` 新增断言；主链路 174 项不变 | 关掉代理端点即可 | 视范围 1–2 周 |

**关键顺序约束**：P0 必须在 P2 之前（否则"行为等价"没有判据）；P1 必须在 P4 之前（否则 Dify 接入会污染 `api/knowledge.py`）；P3 与 P4 可并行。

---

## 8. 风险登记册

| # | 风险 | 触发信号 | 对策 | 回退 |
|---|---|---|---|---|
| R1 | **状态语义被改坏**（追加/替换混用） | 报告出现重复问题项；`发现项 ≥ 5` 变多但质量下降 | 铁律见 §4.1.1；P0 加"同一输入 → 相同 findings 序列"的等价性测试 | 关闭 langgraph 开关 |
| R2 | **步骤表出现重复/缺行** | `步骤数为 8` 断言失败 | 唯一索引 + upsert；`record_step` 只在 `make_agent_node` 内调用 | 同上 |
| R3 | **checkpoint 与业务库不一致**（任务已 done，checkpoint 还在中间态） | 重启后出现"No tasks to run"或重复落报告 | `persist` 幂等（按 task_id upsert）；checkpoint 只用于"未完成"的任务 | `CHECKPOINT=off` |
| R4 | **event loop 绑定**（全局 async checkpointer + `asyncio.run`） | 第二个任务报 "Event loop is closed" / 连接池错误 | 按任务构建 checkpointer（或 P4 后进单 loop worker） | 同上 |
| R5 | **Dify 侧的隐私副本** | 在 Dify 库里能查到简历原文 | 主链路不传原文；运营应用传脱敏副本并关闭日志 | `DIFY_ENABLED=false` |
| R6 | **检索质量没有变好** | A/B 人工评估命中率持平或下降 | 先用 P4 的 A/B 脚本量化；不达标就不切 `RAG_BACKEND=dify` | `RAG_BACKEND=local` |
| R7 | **Dify 运维成本超预期**（容器多、内存紧、升级破坏性） | 本机 15.7GB 内存被吃满、镜像/数据卷占满 C 盘（当前 C 盘仅 34GB 可用） | 单独宿主机或限制容器内存；Dify 的 PG/Redis 与业务复用或外部化；数据卷放 E 盘 | 只保留 P2/P3 的 LangGraph 收益，Dify 延后 |
| R8 | **双写不一致**（知识库同步） | 检索结果里出现已删除的文档 | `knowledge_sync_state` + 软删除 + 定期对账脚本；删除时先删 Dify 再删本地 | `RAG_BACKEND=local` |
| R9 | **框架升级破坏性变更** | 依赖升级后图编译失败 | 锁死版本（`langgraph==x.y.z`）；依赖升级走独立 PR + 全量回归 | 锁版本 |
| R10 | **团队学习成本**（LangGraph 概念与 Dify DSL 都要学） | 改动集中在少数人手上、review 变慢 | P0 的测试是唯一可信护栏；把图结构写成文档（本文 §4.1.2） | 回 legacy |
| R11 | **成本核算失真**（部分调用走 Dify，未回流 tokens） | `/admin/metrics` 的 `cost_per_report` 突然变小 | 规则：任何 LLM 调用必须回到 `workflow_steps`；Dify 运营应用的用量单独统计，不混进主链路指标 | 关闭对应调用路径 |
| R12 | **人工复核被当成"放行通道"** | 出现 fairness < 1 但报告仍 `done` | 红线：公平性不达标一律阻断 | —— |
| R13 | **把 Dify 放进需要重试的写路径** | 重复扣费 / 重复写报告 | Dify **run 不幂等、无 async 模式**（官方原文 "a run isn't idempotent"）→ 本方案只把 Dify 用在**只读检索**与独立运营应用上；写路径一律留在 FastAPI | `RAG_BACKEND=local` |
| R14 | **Dify 升级破坏检索**（内置 Weaviate 跨 12 个 minor 跳版，官方警告可能静默损坏向量检索；1.17.1 还修了一批"静默改变文档内容"的抽取 bug，官方要求重新导入受影响文档） | 检索结果突然变差或变空，且无报错 | 锁版本 + **用外部向量库** + 升级前做检索回归（P4 的 A/B 脚本正好复用） | 回滚镜像 tag |
| R15 | **Dify 出网被 SSRF 代理拦掉** | HTTP 节点无法访问内网服务 | `ssrf_proxy` 默认拒绝私网 → 本方案**不需要 Dify 回调内网**（检索是我们调 Dify，不是反过来），若将来需要，配 `SSRF_PROXY_ALLOW_PRIVATE_IPS` | 不启用相关节点 |
| R16 | **Dify 日志膨胀 + 隐私副本** | 磁盘增长；或在 Dify 库里查到简历原文 | 主链路不传原文；开启日志清理或 `WORKFLOW_NODE_EXECUTION_STORAGE=hybrid`；定期核对 | `DIFY_ENABLED=false` |
| R17 | **无官方混合模式可参照** | 出问题时无法确认真实限制 | 把集成当自建：超时、重试、幂等、降级全部自己写清楚；**不做 Dify ↔ LangGraph 的乒乓调用** | 退化为 B 档 |
| R18 | **把工作流挪到真实队列/独立 worker** | `verify.py` 大批断言在无等待逻辑下失败（`verify.py:140` 依赖 TestClient 同步跑完后台任务） | P2/P3 **保持 `BackgroundTasks`**；要上队列时把"改造 verify.py + 加轮询"单独列为一个阶段 | 回 `BackgroundTasks` |

---

## 9. 成本、资源与投入估算

### 9.1 机器现状（实测）

| 项 | 实测值 | 含义 |
|---|---|---|
| CPU | AMD Ryzen 9 7945HX，16 核 / 32 线程 | CPU 不是瓶颈 |
| 内存 | **15.7 GB** | **瓶颈**：Dify 全套 + 现有前后端 + 浏览器，会紧张 |
| 磁盘 | C: 34.4 GB 可用 / D: 69.2 GB / **E: 60.7 GB** | Dify 镜像 + 数据卷别放 C 盘 |
| 容器运行时 | Docker 已装（`E:\Docker-work`）+ WSL2 | 可用，但按官方要求数据卷放 Linux 文件系统 |

### 9.2 Dify 最小化部署（把十多个容器压到 7–9 个）

不需要全开。按本方案的用途（只用知识检索 + 运营应用），可以关掉：

| 组件 | 处置 | 理由 |
|---|---|---|
| `api_websocket` | **关** | 协作编辑用，我们不用 Dify 画布做业务编排 |
| `agent_backend` + `local_sandbox` | **关** | Agent V2 是实验特性（官方自己警告共享沙箱、仅面向可信用户）；我们用 LangGraph，不需要它 |
| `unstructured`（可选 profile） | **关** | 简历解析仍在 `services/extract.py`；知识文档都是自己写的文本，不需要复杂 ETL |
| `weaviate`（默认向量库） | **换成 `pgvector`** | 复用同一个 Postgres，少一个常驻服务与一份数据备份 |
| `worker_beat` | 可关 | 只在需要定时重建索引时开 |
| **保留** | nginx、web、api、worker、plugin_daemon、sandbox、ssrf_proxy、db、redis（+ 向量能力并入 PG） | 约 **7–9 个容器**；不用 Agent V2 的话 `agent_ssrf_proxy` 也一并省掉 |

内存预算（建议在 compose 里显式写 `deploy.resources.limits`，不要让它们自由膨胀）：

| 容器 | 建议上限 |
|---|---|
| api | 2 GB |
| worker | 2 GB |
| plugin_daemon | 1 GB |
| sandbox | 512 MB |
| web + nginx | 768 MB |
| db_postgres | 1 GB |
| redis | 512 MB |
| **合计** | **约 7.8 GB**（留 8 GB 给宿主、现有前后端与浏览器） |

磁盘：镜像约 8–12 GB，数据卷按知识库规模增长（本项目知识库是十几篇文档，可忽略）。**Docker 的 data root 建议挪到 E 盘**；Dify 的数据卷放 WSL2 ext4，别挂 Windows 目录（官方明确要求）。

### 9.3 投入估算（人日，单人）

| 阶段 | 人日 | 说明 |
|---|---|---|
| P0 基线测试 | 2–3 | 全是新增，无风险 |
| P1 接口抽象 | 2 | `Retriever` / `LLMProvider` |
| P2 LangGraph 串行接管 | 5–7 | 含图、节点包装、幂等步骤表、开关与回归 |
| P3 checkpoint + 僵尸回收 + 清理 cron + 单 thread 锁 | 4–5 | 比"纯接入"多，因为这三项 OSS 不自带 |
| P4 Dify 部署 + 同步器 + 检索切换 + A/B | 6–9 | 含最小化 compose 调通与检索质量评测 |
| P5 运营应用 + 可选 HITL/可观测 | 5–10 | 按应用数量伸缩 |
| **A 档合计** | **约 24–36 人日** | 不含前端改动（本方案前端零改动） |
| **B 档（只做运营型 Dify + 知识库副本）** | **约 5–8 人日** | 不动主链路，风险最低 |

### 9.4 收益怎么算（要可量化，否则不该做）

| 收益 | 度量方式 | 预期 |
|---|---|---|
| 检索质量 | 同一批 query（从 JD 里抽 20 条）在本地混合检索 vs Dify（真实 embedding + rerank）下的人工 top-5 命中率 | 这是**唯一值得为此付部署成本**的指标；不提升就不切 `RAG_BACKEND=dify` |
| 断点续跑 | 在 8 步中间 `kill` 进程，重启后是否续跑且不重复落报告 | 从"整单重跑"变成"从断点续跑"，live 模式省一次全链路 LLM 成本 |
| 延迟 | live 模式下串行 vs 并行（ats/impact/risk）的端到端耗时 | 省约 2 × 单节点耗时；mock 模式零收益 |
| 运营效率 | 非工程同学改知识库/prompt 所需的时间 | B 档就能拿到，是 Dify 的真正卖点 |
| **代价** | Dify 常驻内存 ~8 GB、7–9 个容器、一套新的升级与备份面、2 套观测 | 必须与上面的收益一起看 |

---

## 10. 止损条件：什么情况下**不要**做这件事

工程决策的价值一半在"说不"。满足以下任意两条时，建议**只做 B 档（非主链路 Dify）**，主链路保留现有手写状态机：

1. **主链路始终是 8 节点确定线性链**：近期没有分支、循环、人工介入、断点续跑的规划。现状 `engine.py` 235 行、零依赖、可读性极好 —— 为了"用框架"而引入 LangGraph，是在没有痛点的地方加一层间接。
2. **机器资源已经吃紧**：本机 15.7GB 内存 / C 盘 34GB 可用。Dify 自托管需多个常驻容器，与现有前后端抢资源，收益却主要在"运营便利"而非"业务能力"。
3. **团队就一两个人**：Dify + LangGraph + checkpoint + 数据集同步，是四套新概念。P0 的测试护栏必须先建起来，否则"改完跑不动"的排查成本远高于收益。
4. **知识库检索质量当前不构成瓶颈**：如果真实用户反馈里"检索不准"不是主要投诉，那 Dify 的核心收益就不存在 —— 先把 P3（真实语义向量）用最省事的方式解决（例如直接接一个 embedding 服务的 HTTP 接口 + pgvector），可能比部署整套 Dify 更划算。
5. **没有明确的运营侧使用者**：Dify 的可视化编排与知识库后台，价值来自"非工程同学能自己改"。如果改 prompt 和知识库的人就是开发者本人，`POST /admin/prompts` 与 `seed_knowledge.py` 已经够用。

**反过来说，值得做的信号很明确**：出现"跨步骤的质量回环/人工复核"需求、出现"任务跑一半崩了要重跑"的投诉、出现"知识库检索命中率明显不够"、出现"运营同学要自己改 prompt/知识库"。这四条里占两三条，本方案的 A 档就是合适的。

---

## 附录 A：调研依据与原始记录

本方案里所有"已核查"的平台事实都有出处。原始调研记录（含逐条 URL、源码级证据与 10 条"未查证"清单）保存在：

- [Dify架构调研-原始记录.md](Dify架构调研-原始记录.md) —— Dify 侧，约 15 万字节，含版本/License/自托管/编排/API/RAG/Prompt/可观测/插件沙箱/已知坑/分工分析十节

关键出处（按需点开核对）：

**Dify**
- 知识库检索端点：<https://docs.dify.ai/en/api-reference/knowledge-bases/retrieve-chunks-from-a-knowledge-base-test-retrieval>
- Workflow 运行 API（`inputs` 类型、`response_mode`）：<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow>
- SSE 消费（ping 帧、断线重连）：<https://docs.dify.ai/en/api-reference/guides/streaming>
- Human Input 流程：<https://docs.dify.ai/en/api-reference/guides/human-input-flow>
- Knowledge API（scoped key）：<https://docs.dify.ai/en/api-reference/guides/knowledge>
- External Knowledge API 契约：<https://docs.dify.ai/en/self-host/use-dify/knowledge/external-knowledge-api>
- Docker Compose 部署与最低要求：<https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose>
- 环境变量总表（超时/清理/SSRF/OTel 等）：<https://docs.dify.ai/en/self-host/deploy/configuration/environments>
- 仓库 LICENSE：<https://github.com/langgenius/dify/blob/main/LICENSE>
- 1.17.1 release：<https://github.com/langgenius/dify/releases>

**LangGraph**
- Graph API（State / reducer / Runtime / Command / Send）：<https://docs.langchain.com/oss/python/langgraph/graph-api>
- Persistence / Checkpointers：<https://docs.langchain.com/oss/python/langgraph/persistence>
- Fault tolerance（RetryPolicy / timeout / error_handler）：<https://docs.langchain.com/oss/python/langgraph/fault-tolerance>
- Interrupts（HITL 规则与重放语义）：<https://docs.langchain.com/oss/python/langgraph/interrupts>
- Backward compatibility（改图 = 改 checkpoint 兼容性）：<https://docs.langchain.com/oss/python/langgraph/backward-compatibility>
- Deployment（OSS 与 Agent Server 的边界）：<https://docs.langchain.com/oss/python/langgraph/deploy>
- PyPI：<https://pypi.org/project/langgraph/>、<https://pypi.org/project/langgraph-checkpoint-postgres/>、<https://pypi.org/project/langgraph-api/>（**Elastic-2.0**）
- Windows 已知问题：<https://github.com/langchain-ai/langgraph/issues/8060>（GBK locale，workaround `PYTHONUTF8=1`）、<https://github.com/langchain-ai/langgraph/issues/5029>（CI 无 Windows runner）

**本项目内部依据**
- 契约：[api-contract.md](api-contract.md)、[ZhenResume_代码拆分与生产化建议.md](ZhenResume_代码拆分与生产化建议.md)、[ZhenResume_关键代码与设计原理.md](ZhenResume_关键代码与设计原理.md)
- 需求与验收：`aaa.md`（§3.6 工作流 Agent、§4 非功能需求、§5 验收标准）

## 附录 B：新增依赖与配置片段（示意，落地时以实测为准）

```
# backend/requirements.txt（新增，版本必须钉死）
langgraph==1.2.x
langgraph-checkpoint-sqlite==3.1.x      # 开发/单机
langgraph-checkpoint-postgres==3.2.x    # 生产（依赖 psycopg3）
psycopg[binary]==3.x
```

```ini
# backend/.env（新增）
WORKFLOW_ENGINE=legacy|langgraph
WORKFLOW_PARALLEL=false
WORKFLOW_REWORK_MAX=0
LANGGRAPH_CHECKPOINT=off|sqlite|postgres
LANGGRAPH_CHECKPOINT_URL=
LANGGRAPH_STRICT_MSGPACK=true
ZOMBIE_TASK_TIMEOUT_S=300
PYTHONUTF8=1                 # Windows + 中文 locale 的通用缓解

RAG_BACKEND=local|dify
DIFY_ENABLED=false
DIFY_BASE_URL=http://127.0.0.1:5001
DIFY_DATASET_API_KEY=
DIFY_APP_API_KEY=
DIFY_KNOWLEDGE_DATASET_IDS=
DIFY_TIMEOUT_S=15
DIFY_SYNC_MODE=off|manual|startup
```

> 提醒：`docs/api-contract.md` 是本项目"改接口先改契约"的唯一入口；本方案在 P2–P4 阶段**不需要**新增任何对外端点，P5 的人工裁决端点必须先在契约文档里立项。

