# ZhenResume 性能与价格实测报告（2026-10-20）

> 目的：记录 2026-10-20 两轮性能测试的**全部原始数据**（单份 fixed / 单份 agentic / HR 批量 / 价格成本），并回答「换 PG + Redis + Celery 是否有性能提升」。
>
> 测试全部走**真实模型调用**，不是 mock。所有成本按当前 `.env` 里的**占位单价**计算（见 §6），金额只能横向比较，不是真实账单。

---

## 0. 结论摘要（TL;DR）

| 项目 | 结果 |
|---|---|
| 单份 fixed（本机 SQLite+inline） | **31.6–47.5s**，中位 ≈35s（9 次） |
| 单份 fixed（Docker 全栈 PG+Redis+Celery） | **34.1–47.3s**（3 次，含排队 0.17–1.0s） |
| 单份 agentic（SQLite/全栈） | **75.6–96.6s**，是 fixed 的 2–3 倍；token 约 **10–11 倍**，成本约 **6–7 倍** |
| HR 批量（SQLite+inline） | 10 份 **14.5–15.8s**；50 份 **27.5s**；100 份 **35.2s**，成功率 100% |
| HR 批量（Docker 全栈） | 10 份 **18.7s**；50 份 **71.6s**；100 份 **92.5s**，成功率 100% |
| 批量失败案例（legacy 引擎 + SQLite） | 10 份 **336.1s、9/10 失败**，根因 `database is locked` |
| **换 PG+Redis+Celery 是否有性能提升** | **没有。** 单份无变化（抖动内），批量在本机反而更慢；它带来的是并发安全、任务不丢、可扩展，不是速度 |

---

## 1. 测试环境

### 1.1 硬件与系统

| 项 | 值 |
|---|---|
| OS | Windows 11 家庭版 中文版 10.0.26200 |
| CPU | AMD Ryzen 9 7945HX（16C / 32T） |
| 内存 | 15.7 GB（Docker 可用 7.6 GB） |
| Docker Engine | 29.7.2（Docker Desktop，32 CPU 可见） |
| 宿主 Python | 3.10.8（`backend/.venv`） |
| 容器 Python | 3.12-slim（`backend/Dockerfile`） |
| 关键库 | SQLAlchemy 2.0.36、FastAPI 0.115.6、LangGraph 1.2.15、LangChain 1.4.6、langchain-core 1.6.10、Celery 5.6.3 |

### 1.2 两套运行形态

**环境 A：本机 SQLite + inline（开发形态）**

- 数据库：`backend/data/zhenresume.db`（SQLite，WAL）
- 编排：LangGraph 1.2，`WORKFLOW_ENGINE=langgraph`，`WORKFLOW_PARALLEL=true`
- 执行：`WORKFLOW_EXECUTOR=inline`（FastAPI BackgroundTasks）
- checkpoint：`LANGGRAPH_CHECKPOINT=off`
- 并发：`LLM_MAX_CONCURRENCY=4`、`HR_BATCH_CONCURRENCY=50`

**环境 B：Docker 全栈（生产形态，`docker-compose.local.yml`）**

- 容器：`zhenresume-api` / `zhenresume-worker` / `zhenresume-db`（pgvector:pg17）/ `zhenresume-redis`（redis:7-alpine）
- 数据库：PostgreSQL 17 + pgvector
- 执行：Celery 5.6.3（worker `--concurrency=4`，Redis broker/backend）
- checkpoint：`LANGGRAPH_CHECKPOINT=postgres`
- 编排：LangGraph + 三路并行；`WORKFLOW_MODE` 环境默认 agentic（测试时按任务显式指定 fixed/agentic）
- 访问路径：真实 HTTP API（`http://127.0.0.1:8000/api/v1`，`X-Device-Id` 匿名用户）

> 环境 B 的容器镜像原为 2026-10-19 构建。测试前把当前工作区代码同步进容器并重启，`llm.py / config.py / hr.py / agentic/runtime.py / db.py` 与工作区一致后开测。

### 1.3 模型链与向量模型（测试时生效）

| 角色 | 厂商 | 模型 | 备注 |
|---|---|---|---|
| 对话主层 | 阿里云百炼 DashScope | `deepseek-v4.1-flash-1017` | 8 节点/分析 Agent 全部走它 |
| 对话兜底层 | DeepSeek 官方 | `deepseek-flash` | 两轮测试中**从未触发**降级 |
| 向量主层 | 阿里云百炼 | `qwen3.7-text-embedding-flash`（1024 维） | 未改动 |
| 向量回退 | 本地 Ollama | `mxbai-embed-large:latest`（1024 维） | 测试时 Ollama 未运行 |

模型链健康探针（`scripts/probe_llm`，41 字符提示词）：

| provider | model | 延迟 | tokens in/out | 成本 | JSON |
|---|---|---|---|---|---|
| aliyun | deepseek-v4.1-flash-1017 | **995ms** | 42 / 19 | ¥0.0002 | ✓ |
| deepseek | deepseek-flash | **839ms** | 89 / 32 | ¥0.0004 | ✓ |

vector 探针：`qwen3.7-text-embedding-flash` 返回 **1024 维**，HTTP 200。

### 1.4 关键参数

`LLM_TIMEOUT_S=60`、`LLM_MAX_RETRIES=2`、`LLM_MAX_TOKENS=4096`、`LLM_MAX_CONCURRENCY=4`、`HR_BATCH_CONCURRENCY=50`、`HR_SCREENING=true`（批量走 3 节点轻量图）。

---

## 2. 测试方法与口径

| 场景 | 入口 | 说明 |
|---|---|---|
| 单份 fixed（环境 A） | `scripts.bench_report` 同款驱动 → `workflow.run_analysis` | 真实建任务、跑完整 8 节点、读 `workflow_steps` |
| 单份 fixed / agentic（环境 A） | 临时驱动：建任务时写 `analysis_tasks.mode` | fixed / agentic 由任务级 `mode` 决定 |
| 单份（环境 B） | `POST /api/v1/tasks` → 轮询 `GET /tasks/{id}` | 走真实 Celery 队列；耗时含排队 |
| HR 批量（环境 A） | `scripts.bench_batch` → `_process_batch` | 10/50/100 份，并发上限 50 |
| HR 批量（环境 B） | `POST /api/v1/hr/batches` → 轮询 `GET /hr/batches/{id}` | 上传 100 份唯一简历；每轮独立 JD 避免 7 天去重命中 |
| 环境 B 原始数据 | `backend/data/_perf_stack_results.json` | 单份/批量 API 返回的 usage 原样落盘 |

口径说明：

- 单份「端到端」= 任务创建 → 报告落库；**不含**浏览器上传（另测，见 §5.2）。
- 环境 B 的 `queue` = POST 返回到第一次看到任务 `running`，即 Celery 排队时延。
- 环境 A 的批量耗时 = `_process_batch` 墙钟（与线上同一函数，但跳过 HTTP 与队列）。
- 成本为 `tokens_in/1e6*2.0 + tokens_out/1e6*8.0`（当前占位单价），证据节点成本从报告 `agent_trace` 单独汇总。
- 供应商延迟抖动很大（同一节点 4.7s ↔ 12.2s，批量尖峰可到 40–60s），单次数字不能当稳定值。

---

## 3. 单份诊断

### 3.1 环境 A：单份 fixed（9 次）

| # | 引擎 / 并行 | 样本 | 端到端 | tokens | 成本（占位价） |
|---|---|---|---|---|---|
| 1 | legacy 串行 | 弱 | **33.5s** | 10,428 | ¥0.0421 |
| 2 | legacy 串行 | 中 | **42.8s** | 11,894 | ¥0.0500 |
| 3 | legacy 串行 | 强 | **34.0s** | 11,758 | ¥0.0438 |
| 4 | legacy 串行 | 弱（复测） | **47.5s** | 10,521 | ¥0.0428 |
| 5 | langgraph 并行 | 弱 | **37.6s** | 9,708 | ¥0.0441 |
| 6 | langgraph 并行 | 中 | **35.8s** | 12,155 | ¥0.0519 |
| 7 | langgraph 并行 | 弱 | **32.2s** | 10,857 | ¥0.0453 |
| 8 | langgraph 并行（改完 .env 后） | 弱 | **31.6s** | 10,785 | ¥0.0448 |
| 9 | langgraph 串行 | 弱 | **54.9s** | 11,500 | ¥0.0504 |

| 分组 | 均值 | 中位 | 范围 |
|---|---|---|---|
| legacy 串行（1–4） | 39.5s | 38.4s | 33.5–47.5s |
| langgraph 并行（5–8） | 34.3s | 33.9s | 31.6–37.6s |
| 全部 9 次 | 38.9s | 35.8s | 31.6–54.9s |

**fixed 典型节点明细**（第 8 次，31.6s）：

| 节点 | 延迟 | tokens | 成本 | 状态 |
|---|---|---|---|---|
| parse | 3,714ms | 1,418 | ¥0.0052 | done |
| jd_match | 4,136ms | 1,001 | ¥0.0037 | done |
| ats | 6,122ms | 1,159 | ¥0.0065 | done |
| impact | 5,138ms | 1,189 | ¥0.0051 | done |
| risk | 7,059ms | 1,315 | ¥0.0057 | done |
| suggestion | 9,895ms | 2,415 | ¥0.0118 | done |
| report | 2,483ms | 623 | ¥0.0019 | done |
| qc | 3,207ms | 1,665 | ¥0.0049 | done |
| 各节点之和 | 41,754ms | 10,785 | ¥0.0448 | — |

> 各节点之和 > 端到端，是因为 ats/impact/risk 三路并行重叠。慢节点固定是 `suggestion`（6.6–12.2s）与 `risk/ats/impact`（4.7–9.0s）。

### 3.2 环境 A：单份 agentic（2 次）

agentic 模式会把 jd_match / ats / impact / risk 换成 Agent 自主取证（有界循环），并在 qc 后追加 `agentic_evidence` 节点（非契约，不写 workflow_steps）。

**样本：弱（96.6s）**

| 节点 | 延迟 | tokens | 成本 | 状态 |
|---|---|---|---|---|
| parse | 3,688ms | 1,418 | ¥0.0052 | done |
| jd_match | 16,409ms | 19,796 | ¥0.0484 | done |
| ats | 15,739ms | 21,728 | ¥0.0511 | done |
| impact | 16,201ms | 15,347 | ¥0.0391 | done |
| risk | 23,057ms | 20,271 | ¥0.0503 | done |
| suggestion | 10,254ms | 2,486 | ¥0.0104 | done |
| report | 2,968ms | 622 | ¥0.0019 | done |
| qc | 4,351ms | 1,712 | ¥0.0055 | done |
| **steps 合计** | **92,667ms** | **83,380** | **¥0.2119** | — |
| `agentic_evidence`（额外） | 33,744ms | 23,608 | ¥0.0633 | done，4 次模型往返 / 13 次工具 / 6 条引用 |
| **总计** | **96.6s** | **106,988** | **¥0.2752** | — |

**样本：中（75.6s）**

| 节点 | 延迟 | tokens | 成本 | 状态 |
|---|---|---|---|---|
| parse | 5,165ms | 1,750 | ¥0.0066 | done |
| jd_match | 14,840ms | 18,927 | ¥0.0447 | done |
| ats | 17,242ms | 23,009 | ¥0.0552 | done |
| impact | 19,328ms | 19,699 | ¥0.0499 | done |
| risk | 16,772ms | 20,070 | ¥0.0500 | done |
| suggestion | 10,052ms | 2,539 | ¥0.0102 | done |
| report | 2,364ms | 628 | ¥0.0019 | done |
| qc | 4,017ms | 1,740 | ¥0.0056 | done |
| **steps 合计** | **89,780ms** | **88,362** | **¥0.2241** | — |
| `agentic_evidence`（额外） | 17,852ms | 37,261 | ¥0.0657 | **truncated=True**（8 次往返被预算截断），**0 条引用** |
| **总计** | **75.6s** | **125,623** | **¥0.2898** | — |

### 3.3 fixed vs agentic 对比（环境 A）

| 指标 | fixed（4 次 langgraph 并行） | agentic（2 次） | 倍数 |
|---|---|---|---|
| 端到端 | 31.6–37.6s（中位 33.9s） | 75.6–96.6s（均值 86.1s） | **2–3×** |
| tokens/份 | 9.7k–12.2k | 107k–126k | **~10–11×** |
| 成本/份（占位价） | ¥0.044–0.052 | ¥0.275–0.290 | **~6–7×** |
| 证据引用 | 固定检索 top-k | 额外 6 条（弱样本）/ 0 条（中样本被截断） | — |

> agentic 贵在两点：① 四个分析 Agent 每轮都重发累积上下文，单节点 tokens 15k–23k；② evidence 节点 23k–37k tokens。
> 中样本 evidence 出现「8 次往返被预算截断、0 引用」——不影响报告产出（失败降级为无引用），但说明预算边界会影响引用质量。

### 3.4 环境 B：Docker 全栈单份（真实 API + Celery 队列）

| 模式 | 样本 | 端到端 | 队列时延 | tokens | 成本（占位价） |
|---|---|---|---|---|---|
| fixed | 弱 | **47.33s** | 1,023ms（worker 冷启动） | 12,454 | ¥0.0489 |
| fixed | 中 | **39.52s** | 172ms | 13,590 | ¥0.0540 |
| fixed | 强 | **34.10s** | 180ms | 13,689 | ¥0.0497 |
| agentic | 弱 | **85.85s** | 169ms | 120,568 | ¥0.2790 |

**对比结论**：环境 B 单份 fixed 34.1–47.3s vs 环境 A 31.6–47.5s，差异落在供应商抖动范围内；**换 PG+Redis+Celery 没有让单份更快**，只多了 0.17–1.0s 的队列时延。

---

## 4. HR 批量

### 4.1 环境 A：SQLite + inline（LangGraph + HR 排序表 3 节点/份）

| 份数 | 总墙钟 | 单份墙钟 | 吞吐 | 成功 | steps | tokens | 成本合计 | 单份成本 |
|---|---|---|---|---|---|---|---|---|
| 10 | **15.8s** | avg 12.3 / max 14.6s | 38.0 份/分钟 | 10/10 | 30 | 48,943 | ¥0.1820 | ¥0.0182 |
| 50 | **27.5s** | avg 15.4 / max 26.0s | 109.0 份/分钟 | 50/50 | 150 | 247,458 | ¥0.9206 | ¥0.0184 |
| 100 | **35.2s** | avg 14.3 / max 21.7s | **170.5 份/分钟** | 100/100 | 300 | 493,856 | ¥1.8301 | ¥0.0183 |
| 10（改 .env 后复测） | **14.5s** | avg 12.3 / max 13.6s | 41.4 份/分钟 | 10/10 | 30 | — | ¥0.1801 | ¥0.0180 |

- 100 份分 2 波（并发 50），**满足「100 份 ≤ 60s」验收线**。
- 批量每份 3 节点（parse + jd_match + risk），成本约为单份完整流水线的 **1/3**。

### 4.2 环境 B：PG + Redis + Celery（真实 API）

| 份数 | 总墙钟 | 队列时延 | 成功率 | 吞吐 | 备注 |
|---|---|---|---|---|---|
| 10 | **18.71s** | 543ms | 10/10 | 32.1 份/分钟 | — |
| 50 | **71.56s** | 371ms | 50/50 | 41.9 份/分钟 | 期间 429/ReadTimeout |
| 100 | **92.49s** | 294ms | 100/100 | 64.9 份/分钟 | 期间 429/ReadTimeout |

环境 B 批量任务级统计（10 + 50 + 100 共 160 份）：

| 指标 | 值 |
|---|---|
| 任务平均墙钟 | 20.5s |
| 任务最大墙钟 | 72.8s |
| parse 平均 / 最大 | 6,325ms / 25,038ms |
| jd_match 平均 / 最大 | 7,530ms / 57,735ms |
| risk 平均 / 最大 | 8,909ms / 59,992ms |
| 告警统计（worker 日志） | 429 × 4、ReadTimeout × 1、degraded × 3、retry × 1 |
| checkpoint 表体积（PG） | checkpoints 12MB / checkpoint_writes 18MB / checkpoint_blobs 21MB |

**结论**：批量在本机 Docker 全栈下比 SQLite+inline **慢 2–3 倍**，但主要原因是测试期间**供应商 429 限流与延迟尖峰**（任务最大 72.8s、单节点最大 60s），不是 Celery 排队（0.29s）或 PG 写入（见 §5.3）。

### 4.3 失败案例：legacy 引擎 + SQLite 批量（第一轮测试）

| 指标 | 值 |
|---|---|
| 份数 | 10 |
| 总墙钟 | **336.1s** |
| 条目状态 | **done 1 / failed 9** |
| 报告数 | 1/10 |
| 单份平均墙钟 | 335.9s |
| 吞吐 | 1.8 份/分钟 |
| steps | 8/份（legacy 不认 `HR_SCREENING`，跑完整流水线） |
| 成本 | ¥0.0485（仅完成的那 1 份） |

根因（已在日志中验证）：

1. `app/workflow/engine.py::run_workflow` 在整个 8 节点循环期间持有同一个 SQLAlchemy Session/事务，中间夹着 8 次网络 await；
2. SQLite 只允许一个写者，其余 9 份在 `INSERT analysis_tasks` 处等满 30s（`connect_args.timeout=30`）后抛 `database is locked`；
3. 同步 DB 操作阻塞 asyncio 事件循环，把第一份的 parse 延迟推到 **298,107ms**（≈298s）。

处理：`backend/.env` 已切到 `WORKFLOW_ENGINE=langgraph`、`WORKFLOW_PARALLEL=true`，该组合下批量正常（§4.1）。

---

## 5. 辅助基准

### 5.1 模型链路探针

见 §1.3。结论：主模型可用、JSON 正常、无降级；DeepSeek 兜底也真实可用。

### 5.2 上传解析耗时（不含 LLM）

| 文件 | 解析方式 | 耗时 | 提取字符数 |
|---|---|---|---|
| `sample_resume_1_weak.txt` | text/utf-8 | **2ms** | 448 |
| `sample_resume_1_weak.docx` | python-docx | **173ms** | 425 |
| `sample_resume_1_weak.pdf` | pypdf | **377ms** | 442 |

> 单份/批量端到端里还要再加这段（可忽略量级）；图片/扫描件会走本地 OCR，耗时另计。

### 5.3 数据库写入微基准（单条 INSERT + COMMIT）

| 数据库 | 300 次独立 commit | avg | p50 | p95 | max |
|---|---|---|---|---|---|
| SQLite（WAL，本机） | 1,135ms | **3.78ms** | 3.68ms | 4.18ms | — |
| PostgreSQL 17（Docker，宿主经端口映射） | 1,677ms | **5.59ms** | 5.53ms | 6.04ms | 11.21ms |

- 100 份批量约 900–1,500 次写 → SQLite ≈ 3.4–5.7s、PG ≈ 5.0–8.4s（全部串行化时的上限）。
- 实际写操作与 LLM 网络等待重叠，且 PG 支持并发提交，**DB 不是端到端瓶颈**。

### 5.4 PG / checkpoint 对照实验

同一时间窗（15:53–16:01）的 100 份批量：

| 配置 | 总墙钟 | 单份 avg / max | 限流 |
|---|---|---|---|
| SQLite + inline + checkpoint off | **43.4s** | 16.4 / 24.8s | 429 × 2 |
| PG + inline + checkpoint off（第 1 次） | **85.8s** | 18.3 / 84.0s | 未统计（首波 step 出现 40s 尖峰） |
| PG + inline + checkpoint off（第 2 次） | **54.7s** | 18.6 / 43.2s | 429 × 3 |
| PG + Celery + checkpoint postgres（Docker 全栈） | **92.5s** | 20.5 / 72.8s | 429 × 4 + timeout |

- PG 两次 85.8s / 54.7s 差异极大，且第 1 次首波 parse 最大 40.2s、risk 平均 11.6s（第二波只有 13.0s 墙钟）——**供应商抖动主导**，不能据此认定 PG 固有 2× 慢。
- 宿主 venv 缺 `langgraph-checkpoint-postgres`，**checkpoint=postgres 无法在宿主隔离测试**；Docker 全栈一直开着它（写入量见 §4.2），其开销未单独拆分。
- 可确定的只有：Celery 排队 **0.17–1.0s**、PG commit 比 SQLite 每次 **+1.8ms**，这两项都不足以解释批量差异。

### 5.5 Celery 排队时延汇总

| 场景 | queue（POST → running） |
|---|---|
| 单份 fixed 第 1 次（worker 冷启动） | 1,023ms |
| 单份 fixed 第 2/3 次 | 172ms / 180ms |
| 单份 agentic | 169ms |
| 批量 10 / 50 / 100 | 543ms / 371ms / 294ms |

---

## 6. 价格与成本

### 6.1 当前计价配置（**占位值**）

| 配置项 | 值 | 说明 |
|---|---|---|
| `LLM_PRICE_INPUT_PER_M` | **2.0 元 / 百万 token** | 全局兜底单价 |
| `LLM_PRICE_OUTPUT_PER_M` | **8.0 元 / 百万 token** | 全局兜底单价 |
| `ALIYUN_PRICE_INPUT/OUTPUT_PER_M` | 0 / 0 | 0 = 用全局兜底（**不是免费**） |
| `DEEPSEEK_PRICE_INPUT/OUTPUT_PER_M` | 0 / 0 | 同上 |
| embedding 成本 | 未计入 | 报告成本只统计对话 token |

成本公式（`app/services/cost.py`）：

```
cost = tokens_in / 1_000_000 × price_in + tokens_out / 1_000_000 × price_out
```

> ⚠️ 这两轮所有金额都是按 2/8 元算出来的**相对值**。要用于真实账单/「单份 ≤ 0.5 元」验收，必须把上面的单价换成各模型真实价，否则成本看板是假账。

### 6.2 各场景成本

| 场景 | tokens/份 | 成本/份（占位价） | 100 份外推 |
|---|---|---|---|
| 单份 fixed | 9.7k–12.2k | **¥0.044–0.052** | ¥4.4–5.2 |
| 单份 agentic | 107k–126k | **¥0.275–0.290** | ¥27.5–29.0 |
| HR 批量（3 节点/份） | ~4.9k | **¥0.018–0.0184** | **¥1.83** |
| 链路探针（41 字符） | 61 / 121 | ¥0.0002 / ¥0.0004 | — |

### 6.3 本次测试总消耗（估算）

- 真实 LLM 调用约 **1,700–1,900 次**（单份 8 节点 + 160 份全栈批量 × 3 节点 + PG 对照 200 份 × 3 节点 + agentic 分析 Agent 往返 + evidence）。
- 按占位单价折算总成本约 **¥11–12**：其中批量约 **¥9.7**（环境 A 4 轮 + Docker 160 份 + PG 对照 200 份），单份 fixed 约 ¥1.0，agentic 两份约 ¥0.56。
- 未计入：embedding 调用（每次检索/建索引都会打 DashScope `/embeddings`）。

### 6.4 换真实单价时要改的字段

```ini
# backend/.env
ALIYUN_PRICE_INPUT_PER_M=<真实输入价>
ALIYUN_PRICE_OUTPUT_PER_M=<真实输出价>
DEEPSEEK_PRICE_INPUT_PER_M=<真实输入价>
DEEPSEEK_PRICE_OUTPUT_PER_M=<真实输出价>
# 或各层都留 0，只改全局：
LLM_PRICE_INPUT_PER_M=<真实输入价>
LLM_PRICE_OUTPUT_PER_M=<真实输出价>
```

---

## 7. 回答：换 PG + Redis + Celery 对性能有提升吗？

### 7.1 各组件的实际作用

| 组件 | 对**速度**的影响 | 真正带来的价值 |
|---|---|---|
| PostgreSQL | **无提升**。commit 比 SQLite 每次 +1.8ms（约 10 次写/份 → 可忽略）；并发写不再被 SQLite 单写者锁死 | 并发安全（消除 §4.3 的 `database is locked`）、多实例共享、生产级备份/运维 |
| Redis + Celery | **无提升**，反而多 0.17–1.0s 排队；单份/单批仍是同样的 LLM 并发 | web 进程不被长任务占住；重启不丢任务；worker 可水平扩展；多用户/多批次并行 |
| LangGraph checkpoint=postgres | **略降**（每个 superstep 多一次 PG 写；本次 checkpoint 三张表已到 51MB） | 崩溃断点续跑；任务可恢复 |

### 7.2 实测证据

- **单份**：A（SQLite+inline）31.6–47.5s vs B（全栈）34.1–47.3s → **无变化**（抖动内）。
- **批量**：A 100 份 35.2s vs B 100 份 92.5s → **本机更慢**；但 B 期间有 429/ReadTimeout 与 60s 级单节点尖峰，属于供应商侧，不是 PG/Celery。
- **可控项**：Celery 排队 0.29–0.54s；PG 写 +1.8ms/次；两者合计对单份 <0.1s、对 100 份 <10s，解释不了 2–3× 差距。
- **真正的瓶颈**：单份端到端里 8 次串行 LLM 往返占 **>95%**（31.6s 中 8 个节点和占 41.8s，关键路径仍是 parse→jd_match→max(3路)→suggestion→report→qc）；批量瓶颈是「并发份数 × 每份 LLM 延迟」与供应商限流。

### 7.3 建议

1. **想要更快** → 不要换 PG/Celery。优先做：
   - 缩短 `suggestion` / `risk` / `ats` 的输出与上下文（单份最大头的 3 个节点）；
   - 用 `LLM_FAST_MODEL` 把 ats/impact/risk/qc 路由到更快模型；
   - 批量提高 `HR_BATCH_CONCURRENCY`（注意 429；本次 50 并发在 100 份时已出现限流）；
   - 降低 `LLM_MAX_TOKENS`（当前 4096）或压缩提示词。
2. **想要更稳/可扩展/多用户** → 换 PG + Redis + Celery，并调优：
   - PG：把连接池从默认 5+10 调大（如 20+40），长事务用完即释放；
   - 批量场景可考虑 `LANGGRAPH_CHECKPOINT=off`（除非需要断点续跑）；
   - 批量并发先 20–30 试，观察 429 再逐步加；
   - 监控 429/ReadTimeout 与 `workflow_steps.latency_ms` 的 p95。
3. **本地开发/评测**：保留 SQLite + inline 更快、零依赖；Docker 全栈用于联调/演示/上线验证。

---

## 8. 本次测试期间的代码/配置变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `backend/.env` | 增加 `WORKFLOW_ENGINE=langgraph`、`WORKFLOW_PARALLEL=true`、`LLM_MAX_CONCURRENCY=4`、`WORKFLOW_MODE=fixed` | legacy+SQLite 批量锁死；切到生产同款编排 |
| `backend/app/agentic/runtime.py` | `asyncio.timeout` 加 Python 3.10 兼容垫片（回退 `async_timeout`）；超时异常改为 `except (TimeoutError, asyncio.TimeoutError)` | 本地 venv 3.10 无 `asyncio.timeout`，agentic 全节点 0ms 降级 |
| `backend/requirements.txt` | 增加 `async-timeout>=4.0,<6 ; python_version < "3.11"` | 显式声明 3.10 垫片依赖 |
| `backend/app/db.py` | `ensure_auth_columns` 的 `BOOLEAN DEFAULT 0` 改为按方言（PG 用 `DEFAULT FALSE`） | PG 上报 `DatatypeMismatch`，`users.auto_created` 列补不上，API 500 |
| Docker 容器 | `docker cp` 当前代码 + 重建 api/worker（读新 `.env`） | 镜像为 10-19 旧代码；pip 源不可达无法 rebuild |

> 容器内的代码同步是**临时的**：下次 `docker compose up --force-recreate` 会被镜像覆盖，需要正式 rebuild 才能固化。

---

## 9. 复现命令

```powershell
cd E:\ZhenResume\backend
$env:PYTHONUTF8 = "1"

# 单份 fixed（环境 A）
.\.venv\Scripts\python.exe -m scripts.bench_report

# 批量（环境 A）
.\.venv\Scripts\python.exe -m scripts.bench_batch --count 100

# 链路探针
.\.venv\Scripts\python.exe -m scripts.probe_llm

# 环境 B（Docker 全栈）
docker compose -f docker-compose.local.yml --env-file .env.local up -d
# 然后用 HTTP：POST /api/v1/tasks、POST /api/v1/hr/batches，轮询 GET 对应资源
```

原始 API 结果：`backend/data/_perf_stack_results.json`。

---

## 10. 局限与注意事项

1. **价格是占位值**：所有金额只能横向比较，不能作为真实账单；真实单价需按 §6.4 替换。
2. **供应商抖动大**：同一配置多次跑 31.6–47.5s（单份）、35–92s（批量 100），单次结果不能当 SLA；本报告的批量对比受时段影响明显。
3. **checkpoint 未隔离**：宿主 venv 缺 `langgraph-checkpoint-postgres`，无法单独量化其开销；Docker 全栈实测中它是开启状态。
4. **环境 A/B 非严格同条件**：A 用脚本直调函数，B 走 HTTP + Celery + PG；A 的批量数字不含上传与队列，B 含。
5. **批量样本是合成数据**：3 份样例轮换 + 唯一标记，解析结构与真实简历一致但内容多样性有限；`[bench]/[perf]` 数据已写入本地 SQLite 与 PG。
6. **agentic 的 evidence 节点不写 workflow_steps**：其 tokens/成本来自 `agent_trace`，已在 §3.2 单独汇总。
7. **未测**：图片简历 OCR 全链路、PDF 导出、多用户并发混跑、Celery 多 worker 横向扩展、PG 调优后的复测。
8. **清理测试数据**（如需）：

```sql
-- PG：删除两轮压测用户及其数据（按 device_id 前缀）
DELETE FROM users WHERE device_id LIKE 'perf-%' OR device_id LIKE 'bench-%';
-- 级联关系需先删子表；建议先在测试库执行。
```