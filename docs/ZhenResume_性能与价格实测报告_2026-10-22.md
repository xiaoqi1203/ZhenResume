# ZhenResume 性能与价格实测报告（2026-10-22 重写版）

> **本报告取代**：[ZhenResume_性能与价格实测报告_2026-10-20.md](ZhenResume_性能与价格实测报告_2026-10-20.md)。
> 上一版只回答「两轮压测 + 换 PG/Redis/Celery 快不快」；本版在**同一台机器、同一条真实模型链**上，
> 把范围扩到**全方面测试**（契约、功能、安全、质量评测、性能、成本与定价），并把价格口径从「占位单价折算」
> 升级为「官方原价 + 三情景敏感性 + 套餐单位经济」。
>
> - 被测版本：`E:\ZhenResume`，git `afd22e4`（2026-10-22 HEAD，工作区干净）
> - 测试日期：2026-10-22 14:49–17:00（Asia/Shanghai）
> - 真实调用：主层 `aliyun:deepseek-v4.1-flash-1017`，兜底 `deepseek:deepseek-flash`，embedding `text-embedding-v4`。
>   **全部为真实 live 调用，不是 mock**；成本为按配置单价折算的账面值（口径见 §7.1）。
> - 原始数据与逐项证据：`findings/perf-2026-10-22/`（`envA/`、`envB/`、`func/`、`frontend/`、`eval/`、`price/`）

---

## 0. 一页纸结论（TL;DR）

**一句话**：ZhenResume 在当前硬件 + 真实 `deepseek-v4.1-flash` 链上（Env A：SQLite + inline），**满足「单份 ≤60s / ≤0.5 元」「100 份 ≤60s」三条验收线**，
但余量都不大（单份 18s、100 份 7.3s、agentic 成本仅 5.7%）；**性能瓶颈 100% 在 LLM 网络往返**，
换 PG/Redis/Celery 不提升速度（同时段交错对照甚至略快，属噪声）；**成本侧没有亏损套餐，风险在配额**；
**安全仍有上线阻塞项**（短信 dev 模式 + 默认密钥 + 调试接口 = 可伪造任意用户）。

| # | 指标 | 本轮实测 | 验收线 | 判定 |
|---|---|---|---|---|
| 1 | 单份 fixed 端到端（5 次） | **38.6s**（32.3–41.8，p95 41.8） | ≤60s | ✅ 余量 18s |
| 2 | 单份 fixed 成本 / tokens | **¥0.0513** / 13,149 tok | ≤¥0.5 | ✅ 余量 10× |
| 3 | 单份 agentic（2 次） | **112.9s** / **¥0.4498** / 190,155 tok | 无硬线 | ⚠️ fixed 的 2.92× / 8.8× / 14.5× |
| 4 | HR 批量 100 份（并发 50） | **52.7s**，100/100，**¥0.0184/份** | ≤60s | ✅ 余量仅 7.3s |
| 5 | 并发 4 份单份 | **40.6s** 总墙钟，4/4，**3.80×** 加速 | — | ✅ 近线性 |
| 6 | RAG 检索（32 query） | search p50 **232ms**（纯检索 48ms） | — | ⚠️ embedding 占 ~83% |
| 7 | SQLite 单条写+提交 | **2.10ms**（单份 <0.2%） | — | ✅ 非瓶颈 |
| 8 | 全栈（PG+Redis+Celery）交错对照 | A 54.7s vs **B 46.2s**（同时段）；clean 35.9s | — | ✅ 无栈级性能损失 |
| 9 | 契约 / 账号 / HTTP 黑盒 | verify **175/175**、verify_auth **55/55**、HTTP 黑盒 **39/39 + 74/74** | — | ✅ |
| 10 | 历史 P0 安全项 | **2 修复 / 8 仍存在** | 上线 | ❌ 阻塞 |
| 11 | 前端生产构建 | ✅ **3/3 成功**（实际 Node v24.16.0，56–64s） | 可构建 | ✅ |
| 12 | 7 档套餐成本侧 | 毛利率 **75.9%–99.9%**，无亏损档 | — | ✅ 配额有风险 |

**三条最反直觉的结论**：

1. **上一版最硬的检索证据被推翻**：P9 的「BM25 在 recall@8 +8.2pp 显著」在 `text-embedding-v4` 索引下变成 **−3.1pp**；
   但「不合入 BM25」的最终决策依然正确（产品口径前 3 条 −2.9pp）。
2. **换数据库/队列解决的不是速度**：同时段交错对照里全栈反而略快；它买的是并发安全与任务不丢，
   代价是每次 PG 写 +0.7~2.9ms、checkpoint 约 21MB（≈124KB/份）、队列 0.13–1.7s。
3. **看板成本是保守上限不是账单**：占位价 2/8 元恰为百炼**忙时原价**，真实应付约 0.5×（闲时）~1.0×（忙时）；
   但 agentic 的 0.47 元/份已贴近 0.5 元线，**它不应默认全量开启，也不应与 fixed 同价**。

---

## 1. 本次重写了什么

| 维度 | 2026-10-20 版 | 本版（2026-10-22） |
|---|---|---|
| 代码基线 | 10-20 工作区（含临时 `docker cp` 同步） | 10-22 HEAD `afd22e4`：新增 Agentic RAG（P3–P9）、embedding 切 `text-embedding-v4`、按标题切片（55 片）、评测集扩 32 例、账号/编辑器/管理后台 |
| 功能测试 | 只做性能压测 | 契约全量 + 账号鉴权全量 + 真实 HTTP 黑盒 + 前端构建 + 历史 P0 安全复核 + 报告一致性 |
| 质量评测 | 无 | 32 例检索评测、产品口径 top-3 评测、排序 Spearman、Agentic RAG 两臂 |
| 性能 | 单份/批量两套环境 | 单份 fixed×5、agentic×2、批量 10/50/100、4 路并发、RAG/embedding、上传解析、DB 写、全栈 A/B 对照 |
| 价格 | 占位价 2/8 元折算 | **证明 2/8 恰为百炼忙时原价**；官方闲时/忙时/缓存三档价、三情景敏感性、7 档套餐毛利与回本点、看板偏差审计 |
| Cross-check | 单轮 | 每条数字标注口径、样本量、p50/p95、可引用性与前提条件 |

---

## 2. 测试环境

### 2.1 主机与软件

| 项 | 值 |
|---|---|
| OS | Windows 11 家庭版中文版，10.0.26200（Build 26200），64 位 |
| CPU | AMD Ryzen 9 7945HX，16 核 / 32 逻辑核 |
| 内存 | 15.69 GB（测试期间宿主同时运行 Docker Desktop、PyCharm 等，空闲 1.2–2.1 GB） |
| 宿主 Python | CPython **3.10.8**（`backend/.venv`），langgraph **1.2.15**、langchain 1.4.6、langchain-core 1.6.10、SQLAlchemy 2.0.36、FastAPI 0.115.6、pydantic 2.10.4 |
| 容器 Python | 3.12（`zhenresume-backend:local`，2026-10-21 构建；Env B 绑定挂载当前 `backend/` 代码，未重建镜像，原因见 §2.3） |
| 数据库 | Env A：SQLite（WAL，`synchronous=FULL`，`busy_timeout=30s`）；Env B：PostgreSQL 17 + pgvector |
| 队列 | Env B：Redis 7 + Celery 5.6.3（worker `--concurrency=4`，checkpoint=postgres） |

### 2.2 模型链与知识库

| 角色 | 供应商 / 模型 | 备注 |
|---|---|---|
| 对话主层 | 阿里云百炼 `deepseek-v4.1-flash-1017` | 全程命中，未降级；`enable_thinking=false` |
| 对话兜底 | DeepSeek 官方 `deepseek-flash`（= V4.1-Flash-1017 同源） | 探针可用（本机小请求 0.7–1.0s）；Env A 从未触发，**Env B 事故窗口触发 88 次降级**（见 §6.7） |
| 向量主层 | `text-embedding-v4`（1024 维，DashScope） | 2026-10-22 起替代 qwen3.7（该模型欠费） |
| 向量回退 | 本机 Ollama `mxbai-embed-large:latest`（1024 维） | Ollama 在线，压测中未触发 |
| 知识库 | 19 篇文档 / **55 个切片**（按 `##` 标题切分，mean 234 字/片） | 主库实测；Env A 用主库副本，Env B 首次启动自动灌种子 |

模型链体检（`probe_llm --embeddings`）：主层小请求 **3034ms**（JSON ✓），官方兜底 **663ms**；embedding v4/v3 均 1024 维可用，v2/v1 为 1536 维与 `EMBEDDING_DIM=1024` 不兼容。

### 2.3 两套运行形态与镜像说明

| | Env A（开发形态） | Env B（全栈形态） |
|---|---|---|
| 入口 | 直接调用 `run_graph` / `_process_batch`（跳过 HTTP） | 真实 HTTP API（`127.0.0.1:8000`，`X-Device-Id` 匿名） |
| 编排 | LangGraph 1.2，`WORKFLOW_PARALLEL=true` | 同左 |
| 执行 | `WORKFLOW_EXECUTOR=inline`（进程内） | Celery + Redis 队列（worker 4 并发） |
| 存储 | SQLite 隔离副本 `perfA.db` | PostgreSQL 17 + pgvector（全新数据卷，自动建表/灌种子） |
| checkpoint | off | postgres |
| 数据库口径 | 单写者；短事务 | 多连接并发；checkpoint 每 superstep 落 PG |

> **Env B 镜像为何不重建**：本机到 pip/apt 源实测约 160 KB/s，全量重装依赖需 20+ 分钟且会干扰同机性能测试；
> 现有镜像（10-21 构建）的依赖层与当前 `requirements.txt` 一致，因此**用现有镜像 + 绑定挂载当前工作区代码**，
> 并核对了容器内 `app/services/rag.py` 等文件与宿主哈希一致（`envB/raw/container_code_hashes.txt`）。
> 这是本轮 Env B 与「从零 build」的唯一差异，不影响协议/队列/数据库行为，但版本可复现性需以本机镜像为准。

### 2.4 关键参数（两套环境一致）

`LLM_TIMEOUT_S=60`、`LLM_MAX_RETRIES=2`、`LLM_MAX_TOKENS=4096`、`LLM_MAX_CONCURRENCY=4`、
`HR_BATCH_CONCURRENCY=50`、`HR_SCREENING=true`、`SCORING_VERSION=v2`、`RAG_TOP_K=5`、`WORKFLOW_MODE=fixed`（agentic 项按进程/任务覆盖）。

### 2.5 本轮环境的已知缺失（影响可测范围）

1. **宿主无任何 OCR 引擎**（rapidocr / pytesseract / tesseract 均不存在）→ 图片/扫描件只能测「无引擎降级路径」。
2. 宿主 venv 无 `pdfplumber`（代码按需 import）→ 双栏/表格 PDF 的版式重建路径未测，PDF 走 pypdf。
3. 宿主 `pdf_enabled=False`（LibreOffice 路径是容器内路径）→ PDF 导出按 503 `PDF_UNAVAILABLE` 降级分支验收；DOCX 导出正常。
4. 未做多机/长时间稳定性测试；并发只到 4 路单份与 100 份批量。

---

## 3. 测试范围与方法

### 3.1 覆盖矩阵

| 维度 | 测试内容 | 方法/脚本 | 数据量 | 证据目录 |
|---|---|---|---|---|
| 契约与功能 | 端到端契约、上传/解析/任务/报告/分享/导出/HR/错误码 | `scripts.verify`（TestClient，真实 LLM） | ~175 项断言 | `func/` |
| 账号与鉴权 | 注册/登录/短信/改密/登出/401/管理员门禁/数据认领/PUT 简历 | `scripts.verify_auth` | 55 项 | `func/` |
| 真实 HTTP | 独立 uvicorn + 隔离库：非 LLM 段 + LLM 段 + SSE 时序 | `func/http/http_blackbox.py` | 39+ 项 | `func/` |
| 安全回归 | 历史 19 条 P0 中的安全/正确性条目逐项复核 | `p0_security_probe.py` + 真实 HTTP | 10 项 | `func/` |
| 报告一致性 | B-1~B-4（总分/风险等级/空简历/证据 span） | `check_report_consistency.py`（mock） | 20 项 | `func/` |
| 前端 | typecheck / lint / build / 页面冒烟 | pnpm + Edge headless 截图 | — | `frontend/` |
| 检索质量 | recall@5/8/10、BM25 对照、前 3 条产品口径 | `eval_retrieval.py`、`eval_topk_decision.py` | 32 例 × 多臂 | `eval/` |
| 排序质量 | Spearman、假差距 recall、误判 | `eval_sort.py`（rule/oracle；**semantic 本轮未执行**） | 32 份 | `eval/` |
| Agentic RAG | fixed vs agentic 两臂、引用校验、轮次 | `eval_rag.py`、`smoke_agentic_*` | 10 例 | `eval/` |
| 单份性能 | 单份 fixed / agentic 端到端、节点明细 | `bench_report`、DB 导出 | 5 + 2 次 | `envA/` |
| 批量性能 | HR 10/50/100 份、full 对照 | `bench_batch`（Env A）/ HTTP（Env B） | 160 + 10 份 | `envA/`、`envB/` |
| 并发扩展 | 4 份单份同时跑 | `concurrency_4.py` | 4 份 | `envA/` |
| 辅助基准 | 模型/embedding 探针、RAG 检索、上传解析、DB 写 | `probe_llm`、`rag_latency.py`、`upload_parse.py`、`db_write.py` | 32 query / 6 文件 / 300 写 | `envA/` |
| 全栈对照 | PG+Redis+Celery 真实 HTTP 单份/批量 | `bench_http.py` | 3 clean + 1 agentic + 160 批量 + 4 交错 | `envB/` |
| 成本与定价 | 官方单价、单位成本、套餐毛利、口径偏差 | 代码审计 + 官方定价页 + `calc.py` | 7 套餐 × 3 情景 | `price/` |

### 3.2 口径定义（读数字前先看）

- **端到端**：任务创建 → 报告落库。Env A 为 `run_graph` 墙钟；Env B 为 `POST /tasks` → `status=done`（含 Celery 排队）。
- **队列时延**：Env B 的 `POST → 首次 status=running`。
- **节点和**：`workflow_steps.latency_ms` 之和；含并行重叠时会大于端到端。
- **tokens**：`workflow_steps.tokens` = **输入 + 输出**总 token（不是输出 token；`bench_report` 打印的 `out=` 标签有误）。
- **成本**：`tokens_in/1e6×p_in + tokens_out/1e6×p_out`，默认 `p_in=2.0`、`p_out=8.0` 元/百万 token（= 百炼忙时原价，见 §7.1）。
  agentic 的 `agentic_evidence` 是非契约节点、不写 `workflow_steps`，其 tokens/成本从报告 `extra_json.agent_trace` 单独汇总后相加。
- **串行原则**：性能项全部**串行执行**，不与其他 LLM 任务并行；功能/评测项在性能阶段结束后并行执行，标注了时间窗。
- **隔离**：所有写库测试使用主库副本（`perfA.db`、`func.db`）或全新 PG 卷；主库 `backend/data/zhenresume.db` 未被基准写入。

---

## 4. 功能、契约与安全回归

### 4.1 通过率总表

| # | 测试 | 结果 | 耗时 | 备注 |
|---|---|---|---|---|
| 1 | `scripts.verify_auth`（账号/鉴权/门禁/认领） | ✅ **55/55** | 5.8s | 含 seed 的 embedding 调用，无 LLM chat |
| 2 | `scripts.check_report_consistency`（B-1~B-4） | ✅ **20/20** | 5.1s | mock 规则引擎 + 临时库 |
| 3 | `scripts.check_fix_round` | ✅ **6/6** | <1s | 择一识别/年龄性别合规/兜底过滤 |
| 4 | `scripts.check_round2` | ✅ **5/5** | <1s | 按比例计分/去重/近义建议 |
| 5 | 真实 HTTP 黑盒 · 非 LLM 段 | ✅ **39/39** | 1.9s（+启停 4s） | uvicorn:8010 + 隔离库；health/上传/错误码/auth/导出/IDOR |
| 6 | 契约/错误码探针 | ⚠️ **17/19** | 2.3s | 2 项失败是历史 P1-27（路由级 404/405 未统一结构） |
| 7 | P0 安全复核（历史 10 项断言） | ⚠️ **2 FIXED / 8 EXISTS**（A-1、A-1b 同源，矩阵合并为 1 行） | 2.5s | A-1 已修复；A-2/A-3/A-4/A-5/静态挂载仍存在 |
| 8 | `scripts.verify` 全量契约 | ✅ **175/175** | 219.9s | 真实 LLM；成本 ¥0.2285；见 §4.5 |
| 9 | 真实 HTTP 黑盒 · LLM 段 | ✅ **74/74** | 193.8s | POST/SSE/报告/分享/HR/CSV；见 §4.5 |
| 10 | `scripts.check_ocr` | ⚠️ **0/3** | 0.6s | 宿主无 OCR 引擎（环境事实，非缺陷） |
| 11 | 前端 typecheck / lint / build | ✅ / ✅ / ✅ **3/3** | 2.5s / 8.6s / 56–64s | Node v24.16 + Next 14；见 §4.6 |

### 4.2 历史 P0 安全项回归矩阵

> 证据：`func/raw/p0_security_probe.{json,log}`（TestClient，真实路由与依赖，隔离库，零 LLM）；
> 真实 HTTP 层复核：`func/raw/http_blackbox.nollm.json`。所有请求/响应原文可回放。

| 编号 | 历史问题 | 本轮结论 | 关键证据 |
|---|---|---|---|
| **A-1** | 管理后台 4 个端点无鉴权（含 A-1b：普通登录用户也能看后台） | ✅ **已修复** | 匿名 `GET /admin/metrics|prompts|feedback`、`POST /admin/prompts` → 全部 **403** `{"code":"ADMIN_REQUIRED"}`；普通 jobseeker 账号同样 403 |
| **A-2** | `SMS_DEV_MODE` 默认开启，验证码回传响应体 | ❌ **仍存在** | `POST /auth/sms-code` → 201 `{"dev_code":"123456"}`；`POST /auth/login-sms {code:"123456"}` → 200，任意手机号自动建号（`config.py:253` 默认 True，`.env` 未覆盖） |
| **A-2b** | `/auth/_debug/accounts` 泄露全站账号 | ❌ **仍存在** | 匿名 `GET /auth/_debug/accounts` → 200，返回 **39（TestClient）/ 41–43（HTTP，随时间增长）** 条账号（user_id/email/phone/role） |
| **A-3** | `AUTH_SECRET` 留空时用公开常量签 JWT | ❌ **仍存在** | 用常量 `dev-insecure-secret-change-me` 离线签 `sub=<真实 user_id>` → `GET /auth/me` **200**；随机密钥签同一 payload → 401 |
| **A-3 组合** | 与 A-1 叠加：伪造 admin token | ❌ **仍存在** | 伪造 `sub=<真实 admin id>` → `GET /admin/metrics` **200**（A-1 的 403 只挡住不伪造 token 的人） |
| **A-4** | 支付回调零验签 | ❌ **仍存在** | 匿名 `POST /payments/callback {"status":"paid"}` → 200 且 DB 置 paid；再推 `refunded` 仍为 paid（不可回滚） |
| **A-5** | 知识库写接口开放，可污染共享 RAG | ❌ **仍存在** | 匿名（device）`POST /knowledge/docs` → 201 并写入切片；仍用 `get_current_user` 而非 `require_admin` |
| **P0-17** | `/static` 挂载无归属校验（简历原件可直链） | ❌ **仍存在** | 上传后匿名 `GET /static/<stored>` → 200，字节与原文件一致 |
| **附加** | `file_url` 与挂载根不一致（死链） | ❌ **仍存在** | `file_url=/static/uploads/<name>` 原样访问 → 404（挂载根是 `upload_dir`，多一层 `/uploads`） |

> **结论**：上一轮审计后，**A-1（管理后台鉴权）与 B 类报告正确性已修复**；
> 但只要 `SMS_DEV_MODE=true` + `AUTH_SECRET` 空 + `_debug` 泄露账号三者同时成立，
> 攻击者可以离线伪造任意用户（含管理员）JWT —— 这是**当前运行配置下最严重的安全面**，
> 与 A-1 是否修复无关。生产上线前必须同时关掉这三项。

### 4.3 报告正确性（B-1~B-4）

`check_report_consistency`（mock 规则引擎、临时库）**20/20 PASS**：

| 编号 | 历史问题 | 本轮结论 | 证据 |
|---|---|---|---|
| B-1 | 摘要总分 ≠ `scores.overall` | ✅ 已修复 | 摘要「综合得分 60/85/92 分」与 overall 一致 |
| B-2 | 风险等级与红色条目冲突 | ✅ 已修复 | 3 红→red、1 红→yellow、0 红→green（`risk_level` 吸收全维度红色） |
| B-3 | 空简历也能出「中等水平」 | ✅ 已修复 | 空文本/纯空白/纯英文被 `analyze_guard` 拦截，`POST /tasks` → 400 `RESUME_UNANALYZABLE` |
| B-4 | `evidence.span ≠ quote` | ✅ 已修复 | 三份报告 span 切片 == quote，红条均可在声明来源定位 |

### 4.4 错误结构与边界

- 资源级 400/401/402/404/413/415/503 全部为统一 `{detail:{code,message}}`；
  402 `QUOTA_EXCEEDED` 通过「临时开闸 + 隔离库提额」验证，在入队 LLM 前拦截（零成本）。
- 路由级 404/405（历史 P1-27）仍是 FastAPI 默认 `{"detail":"Not Found"}` —— **未修复**。
- 上传路径穿越被挡住（落盘 uuid）；空文件 400；11MB → 413（但仍是**整份读入内存后**判断，历史 P1-6 未变）。
- 删除简历：`DELETE /resumes/{id}` 已实现（归属 404 + 文件 unlink + 级联）。
- 分享接口已按契约支持 `GET|POST`。
- 内容级去重（L2）：同内容 txt→docx 命中 `extract_method=dedup`，复用首份解析（抽取仍真实发生）。

### 4.5 全量契约与真实 HTTP（LLM 段）

**`scripts.verify` 全量：175/175 通过**（真实 LLM，隔离库，**219.9s**，成本 **¥0.2285**，38 次 LLM 调用全部命中 aliyun 主层、零降级；PDF 走降级分支少 2 项检查，符合契约 §2.4）。
覆盖：上传/解析（txt/docx/pdf）、JD 解析、8 节点工作流、报告五维分/风险等级/≥5 建议、证据引用、
编辑器闭环、二次诊断一致性、公平性、分享、导出（DOCX 200 / PDF 503 降级）、HR 批量、额度与错误码。

**真实 HTTP 黑盒（uvicorn:8010 + 隔离库 + inline）：74/74 通过**（`func/raw/http_blackbox.{json,md}`）。
其中关键的端到端断言：

| 段 | 结论 | 证据 |
|---|---|---|
| POST /tasks | 202 立即返回（0.015s），不阻塞 | task_id 正常，轮询可推进 |
| **SSE 真流式** | ✅ **不是伪流式**：8 个 `step_done` 在 2.0s–102.0s 之间分批到达，跨度 **100.0s**；done@106.6s | 见 `http_blackbox.md` 时序表 |
| 报告契约 | 五维分 60/54/30/95/61/78、risk_level=red、findings=8 条且按 red→yellow→green 排序、摘要总分 == overall | 与 B-1/B-2 修复一致 |
| RAG 生效 | `knowledge_refs=3` | fixed 线固定检索 |
| 分享 | GET 创建 200 → 匿名读 200 → 手机号/邮箱打码 → DELETE 204 → 撤销后 404 `SHARE_NOT_FOUND` | 契约 §5.2 |
| DOCX 导出 | 200，37.5KB，合法 ZIP（PK 头） | PDF 503 `PDF_UNAVAILABLE`（宿主未配 LibreOffice） |
| HR 3 份 | 202 → 3 个 `item_done` → done，按总分降序 `[74,60,60]`，CSV 带 BOM | 强弱有区分度 |
| 鉴权 | 改密后旧 token 401 `AUTH_REVOKED`；登出失效；匿名 401；普通账号/匿名 admin 均 403 | A-1 修复在 HTTP 层成立 |
| IDOR | 跨用户读简历/报告/任务全部 404 | 归属校验完整 |
| P0 复现 | 短信 dev_code、`_debug/accounts` 43 条、匿名支付回调 paid、知识库匿名写入并排到检索第 1、`/static` 直链 200 | 与 §4.2 一致 |

> 注：本次 HTTP 任务单份耗时 106.6s，同窗口（16:16–16:19）供应商再次偏慢（3 次 504/ReadTimeout，其中 2 次降级到 `deepseek-flash`）；该数字不代表稳态性能，仅用于功能断言。
> **跨模型不可比**：同一输入两次 HTTP 跑，五维分完全一致（60/54/30/95/61/78），但 `risk_level` 一次 yellow（红 2）一次 red（红 3）。
> 结论：**分数可回归，risk_level 在 live 模型下有随机性**（它是 findings 的派生量；mock 模式完全一致，20/20）。引用分数时请只引用五维分与 overall，不要引用 risk_level 做稳定性证明。

### 4.6 前端构建与页面冒烟

| 项 | 结果 | 说明 |
|---|---|---|
| Node / pnpm | 系统 **v24.16.0**；bundled v24.21.0 + pnpm **11.28.5**；`pnpm exec node -v` = **v24.16.0** | `next build` 子进程实际跑在系统 Node v24.16.0（正是要验证的组合） |
| `tsc --noEmit`（typecheck） | ✅ **通过** | 无输出即无类型错误 |
| `next lint` | ✅ **No ESLint warnings or errors** | — |
| `next build` ×3 | ✅ **3/3 成功**，耗时 61.9s / 56.1s / 63.6s | 每次先删 `.next`；**未复现历史「5 次 3 败」** |
| 构建产物 | 13 个路由；首屏 JS 87.1KB，首页 119KB | `/analyze/[taskId]`、`/report/[reportId]` 为动态路由 |
| 页面状态 | `/` `/pricing` `/hr` `/user` `/admin` `/login` 全部 **200**；`/analyze`、`/report` 裸路径 **404**（缺动态参数，符合路由设计） | 截图：`frontend/shots/home|pricing|hr-*.png` |

> 页面冒烟时后端 API 未就绪（8010 尚未启动），页面自身仍 SSR 返回 200，API 请求失败由前端错误态承接 —— 说明**前端可独立启动、不依赖后端可用性**；但也意味着「页面 + 真实后端」的联调应在部署后另做一次。

> **结论修复**：上一轮审计（10-20）记录的「Node 24 + Next 14 构建 flaky（5 次 3 败）」在本轮 **3/3 通过**（`next build` 子进程实际跑在系统 Node v24.16.0 上）。
> 说明失败与具体环境/时点有关，而非代码不可构建；生产 Docker 镜像内 Node 版本应固定并在 CI 复跑验证。

**契约比对**：前端 20+ 组 API 调用与后端路由逐项对齐（含 SSE 帧格式、`X-Device-Id` 鉴权、下载走 fetch+blob）。
发现的差异均非阻塞：① pricing 页静态兜底文案与后端 `PLANS` 有 7 处名称/features 漂移（后端恢复即覆盖）；② 契约 §5.2 写 `GET /reports/{id}/share`，前端只用 POST（后端 GET/POST 都接受）；③ 账号中心「订单列表」是占位文案，后端无 `GET /payments/orders`；④ 网络错误提示写死 `127.0.0.1:8000`，与本机 `192.168.10.128` 不一致。详见 `frontend/summary.md` §5。

---

## 5. 质量评测

> 评测集：`eval/sort_fixtures/` **32 例**（A/B/C/D 各 8，按覆盖度分档）× 1 JD；知识库 19 docs / 55 chunks。
> gold v2 由规则层结论自动推导（P9 起），含 O*NET / McMaster / Dartmouth 外部语料。
> 本轮全部使用 **text-embedding-v4** 索引（14:12 重建，55 片不变）。

### 5.1 检索器级评测（32 例，v2 gold）

| top_k | 基线 v2 | bm25_0.5 | bm25_0.7 | mmr_0.7 | 历史（P9） |
|---|---|---|---|---|---|
| recall@5 | **29.3%** | 30.5%（+1.2） | 30.5%（+1.2） | **38.2%（+8.9）** | 28.7%，BM25 +0.0 |
| recall@8 | **47.8%** | 45.3%（−2.5） | 44.7%（**−3.1**） | 47.8%（0.0） | 38.2%，bm25_0.7 **+8.2（显著）** |
| recall@10 | **56.0%** | 53.5%（−2.5） | 50.5%（−5.5） | — | 56.0%，bm25 −9.6 |

- 统计效力：基线 SE **0.51–0.58pp**，95% CI **±1.0–1.1pp**（32 例）→ P9「SE≈0.5pp」成立。
- **P9 的「k=8 上 BM25 +8.2pp 显著」无法复现，方向反而为负**；recall@10 的基线 56.0% 与 P9 完全一致。
- 最终判定不变且更强：**BM25/MMR 重排没有稳定收益，不应合入生产**（生产 `rag_top_k=5`，BM25 在 k=5 仅 +1.2pp，低于 CI）。

### 5.2 产品口径：真正注入 prompt 的前 3 条

| 指标 | 本轮（32 例） | 历史（P8/P9） | 判定 |
|---|---|---|---|
| 前 3 条 recall | **26.2%** | 20.1% / 19.1% | 随 v4 embedding 上升 |
| k=3/5/8/10/12 下前 3 条 recall | **恒定 26.2%** | 恒定 19.1% | ✅ P7「改 top_k 零收益」成立 |
| prompt 字符 | 恒定 **890** | 978 | 改 k 不影响 token 成本 |
| 前 3 条 scope 覆盖 | 1.69 | 2.00 | 漂移 |
| BM25 重排后前 3 条 | 23.3%（**−2.9pp**） | 19.1%（0.0） | ✅「BM25 对产品口径无收益」成立（现为负） |

> **方法论点**：`recall@k` 与「用户看到的前 3 条」没有因果关系（P7 已证）。本轮再次验证：
> 即使 recall@5 上 mmr_0.7 +8.9pp，前 3 条反而 −2.9pp。**只按 recall@k 做优化会做错决定。**

### 5.3 排序质量（32 例，rule / oracle）

| 口径 | Spearman（设计覆盖 vs ATS） | ATS 组均值 A/B/C/D | 历史（P2-b） |
|---|---|---|---|
| 规则 v1 等权 | **+0.825** | 68.2 / 67.5 / 64.0 / 60.2 | +0.859，71.0/70.5/68.2/62.2 |
| 规则 v2 分层 | **+0.796** | — | +0.835 |
| oracle 语义上界 | **+0.939** | 救回 +8.5 / +8.0 / +3.0 / +1.5 | +0.980 |

- 组间单调性 A≥B≥C≥D ✅；假差距 recall（oracle）100%。
- 与 P2-b 相比：**绝对分整体下移 3–5 分、Spearman −0.03~−0.04** —— 方向性结论（语义救回、单调性、救回幅度随质量递减）仍成立，但**具体数值不可跨版本引用**。
- **`--engine semantic` 本轮未执行**（PowerShell 包装脚本异常导致前两步被静默跳过；16:26 按时间上限放弃，未补跑）。因此本节的排序结论只由 rule/oracle 两个无 LLM 臂支撑；semantic 的「误判减少/成本 16×」等历史数字本轮未复核。

### 5.4 Agentic RAG（fixed vs agentic 两臂）

> **执行状态（诚实声明）**：`eval_rag.py` 两次运行都在 agentic 臂崩溃（原因见 §5.4.2），
> 第一轮完成 9/10 例（缺 `rag_D06`），下表由逐例输出手算，**不是脚本汇总**，不能与 P3 的 10 例 summary 严格横比。
> `eval_sort --engine semantic` 因 PowerShell 包装脚本异常未执行（已如实记录，不补跑）。

| 指标 | 历史（P3，11 docs/16 chunks） | 本轮（9/10 例，55 chunks） | 判定 |
|---|---|---|---|
| gold 覆盖 · fixed（1 次 top_k=5） | 89.2% | **88.0%** | 基本持平 |
| gold 覆盖 · agentic 多轮并集 | 96.7%（另一轮 93.3%） | **87.1%** | ❌ **无法复现**（并集不再优于 fixed） |
| gold 覆盖 · agentic 第 1 轮同预算 | 55.0% | 48.3% | 漂移（噪声大） |
| 至少引到 1 条 gold | 100% | **100%（9/9）** | ✅ |
| 引用命中 gold（宽口径） | — | 30.8% | 新增数字 |
| 轮次 | 3.0–3.9 | **3.0**（9 例全部） | ✅ |
| 成本 / 延迟（每例） | ¥0.07–0.09 / 23–28s | **¥0.093 / 40.6s** | 成本持平、延迟 +~13s |

**5.4.1 结论**：Agentic RAG 的**能力**仍然成立（多轮检索 → 可校验引用 → 挂载报告，见 `smoke_agentic_rag`：
8 条 findings、8 条引用挂载、0 未匹配、quote 全部逐字可定位），但**「检索广度显著优于 fixed」的旧结论不成立**。
价值应表述为「把没有引用做到有引用、把检索决策可解释」，而不是「覆盖更多 gold」。

**5.4.2 本轮发现的真实缺陷（降级链 × Agent 的兼容性问题）**

两次 `eval_rag` 崩溃的根因相同：

```
Aliyun 主层超时/失败
  → LangChain ModelFallbackMiddleware 切到 DeepSeek 官方 deepseek-flash
  → 该模型默认 thinking 模式，要求把 reasoning_content 回传给 API
  → LangChain 适配层未回传
  → HTTP 400 "The reasoning_content in the thinking mode must be passed back to the API"
  → eval_rag.py 没有 per-case try/except → 整轮进程崩溃
```

- 影响面：**任何走 agentic + 触发兜底模型的请求**都可能 400；fixed 线的 `complete_json` 不走 LangChain 中间件，未受影响。
- 次生问题：`eval_rag.py` 一例失败即丢整轮 summary，评测脚本应加 per-case 容错。
- 建议（未改动代码）：证据/分析 Agent 的兜底模型避开 thinking 模式，或显式回传 `reasoning_content`；同时给评测脚本加单例容错。

**5.4.3 端到端 smoke**

| 脚本 | 结果 | 关键证据 |
|---|---|---|
| `smoke_agentic_rag` | ✅ **OK** | 多轮 4 请求/3 执行/1 拦截、模型往返 5、工具 10；**引用 8/8 全部校验通过、挂载 8/8**；¥0.0801 / 100.9s |
| `smoke_agentic_report` | ✅ **OK** | agentic 单份诊断 **8 步契约不增不减**（`agentic_evidence` 不进 `workflow_steps`）；模型往返 5、工具 10、检索 4/3/1；**引用 10/10 通过、挂载 10/10**；报告 7 findings/6 带引用；整条工作流 179.1s，agent ¥0.0834/18.5s |

### 5.5 漂移主因与可复现性结论

1. **检索数字的漂移主因是 embedding 换模型**：P6–P9 的评测运行（当日 08:42–10:21）使用旧向量（qwen3.7 / 本地 mxbai），11:38–14:12 知识库已用 `text-embedding-v4` 重建（55 片不变、1024 维）。同一评测集、同一 gold、同一检索器，**只换 query/doc 向量，recall@8 从 38.2% 变 47.8%**。
2. 因此 **P9 的「bm25_0.7 在 k=8 +8.2pp 显著」不成立**（本轮 −3.1pp）；但 P9 的**最终决策「不合入 BM25」依然正确且更强**。这是一次「证据被推翻、结论仍成立」的典型案例。
3. **排序绝对分随评分实现变化**：Semantic 判定的救回幅度与 ATS 基线较 10-16 已变，跨版本只能比方向、不能比数值。
4. **可复现性要求**：任何检索/排序结论必须同时记录 **embedding 模型 + 索引重建时间 + 评测集版本**，否则三个月后无法解释数字漂移。

---

## 6. 性能实测

> 性能项全部**串行**执行；Env A 时间窗 14:59–15:15，Env B 时间窗 15:23–15:50。
> 成本为账面值（2/8 元每百万 token）；tokens 为输入+输出。

### 6.1 模型链探针

| provider | model | 延迟 | tokens in/out | 成本 | JSON |
|---|---|---|---|---|---|
| aliyun（主） | `deepseek-v4.1-flash-1017` | **3034ms** | 42 / 19 | ¥0.0002 | ✓ |
| deepseek（兜底） | `deepseek-flash` | **663ms** | 89 / 34 | ¥0.0004 | ✓ |

- embedding：`text-embedding-v4` 1024 维 ✓；`text-embedding-v3` 也 1024 维可用；v2/v1 为 1536 维（与 `EMBEDDING_DIM=1024` 不兼容）。
- 压测期间主层全程可用；Env A 的 640 次 LLM 调用**全部命中主层、零降级**。

### 6.2 单份 fixed（5 次，弱/中/强/弱/中）

| # | 样本 | 端到端 | 节点和 | tokens | 成本 | overall | risk_level |
|---|---|---|---|---|---|---|---|
| 1 | weak | 40.73s | 50.3s | 12,909 | ¥0.0524 | 60 | yellow |
| 2 | medium | 32.34s | 43.8s | 12,894 | ¥0.0491 | 85 | yellow |
| 3 | strong | 39.55s | 44.3s | 12,904 | ¥0.0437 | 91 | yellow |
| 4 | weak | 38.80s | 50.2s | 13,336 | ¥0.0556 | 60 | red |
| 5 | medium | 41.77s | 55.5s | 13,702 | ¥0.0556 | 85 | yellow |

- **端到端 mean 38.64s（32.34–41.77，stdev 3.31s / 8.6%）**；tokens mean 13,149；成本 mean **¥0.0513**。
- 每份恰好 **8 行** `workflow_steps`，状态全 `done`，**无降级、无重试、无失败**。
- 分数跨 5 次稳定：weak 60/60、medium 85/85、strong 91 → 排序与区分度可复现。
- 同一 weak 样本的 `risk_level` 在 yellow/red 之间跳、strong 在 yellow/green 之间跳：**总分稳定、risk_level 有措辞级抖动**（LLM 判定），引用分数时不要用 risk_level 当稳定指标。

**节点延迟（5 次 cross-run）**

| 节点 | min | mean | p50 | max | max/min | tokens/次 | 成本/次 |
|---|---|---|---|---|---|---|---|
| parse | 3.13s | 4.39s | 4.38s | 5.20s | 1.66× | 1,700 | ¥0.0063 |
| jd_match | 3.53s | 4.85s | 4.87s | 5.87s | 1.66× | 1,729 | ¥0.0060 |
| **ats** | 4.04s | 8.27s | 8.46s | 11.73s | **2.90×** | 1,275 | ¥0.0068 |
| impact | 3.40s | 6.03s | 6.57s | 7.08s | 2.08× | 1,799 | ¥0.0067 |
| risk | 6.51s | 7.46s | 7.00s | 8.66s | 1.33× | 1,451 | ¥0.0062 |
| **suggestion** | 7.45s | **11.26s** | 11.00s | 15.34s | 2.06× | **2,770** | **¥0.0118** |
| report | 2.54s | 2.96s | 2.95s | 3.37s | 1.33× | 619 | ¥0.0019 |
| qc | 2.84s | 3.61s | 3.37s | 4.46s | 1.57× | 1,806 | ¥0.0056 |

- **关键路径**：`parse 4.39 → jd_match 4.85 → max(ats 8.27, impact 6.03, risk 7.46) → suggestion 11.26 → report 2.96 → qc 3.61 ≈ 35.3s`；
  端到端均值 38.6s − 关键路径 35.3s ≈ **3.3s** 为非契约开销（建任务/检索/落库/收尾）。
- **并行真实生效**：节点和 mean 48.8s > 端到端 38.6s，节省 **10.2s（20.9%）**。若串行约 52s，仍 <60s 但余量只剩 8s。
- **最慢节点 suggestion**：mean 11.26s（占总时长 29%）、¥0.0118（占成本 23%），因为输出最长（2,770 tok/次，其他节点的 1.5–4.5×）。
- **抖动最大 ats**：4.04–11.73s（2.90×），同一 prompt/模型，纯供应商排队波动。

### 6.3 单份 agentic（2 次）

`WORKFLOW_MODE=agentic` 把 jd_match / ats / impact / risk 换成 Agent 自主取证，并在 qc 后追加**非契约节点** `agentic_evidence`（不写 `workflow_steps`，开销从报告 `extra_json.agent_trace` 取）。

| 运行 | 端到端 | steps tokens | steps 成本 | evidence 墙钟 | evidence tokens | evidence 成本 | 合成总 tokens | 合成总成本 |
|---|---|---|---|---|---|---|---|---|
| weak | **114.36s** | 129,067 | ¥0.3106 | 33.2s | 50,667 | ¥0.1176 | **179,734** | **¥0.4282** |
| medium | **111.50s** | 148,886 | ¥0.3541 | 31.9s | 51,689 | ¥0.1172 | **200,575** | **¥0.4713** |
| 均值 | 112.9s | — | — | 32.6s | — | — | 190,155 | **¥0.4498** |

**相对 fixed：时间 2.92× / tokens 14.5× / 成本 8.8×**（上一版报告为 2–3× / 10–11× / 6–7×；本轮 evidence 上下文明显更长，tokens 倍数升至 14.5×）。

- evidence 节点：7 次模型往返、12 次工具调用、**3 轮检索 / 18 个命中切片**、2 个方向被预算拦截、`truncated=false`。
- 引用质量：**14/14、12/12 全部通过 provenance 校验**（chunk 存在 + quote 逐字可定位），attached 14/12、unmatched 0。
- **但 agentic 分析节点自身的引用有损耗**：impact 4/5、risk 3/5、ats 2/5 通过（失败原因集中在「引用了本轮检索没返回过的 chunk」）→ 模型编 chunk_id 仍会发生，**服务端 provenance 校验是必需的最后一道闸**。
- evidence 的一半时间压在**最后一轮 finish**：16.1s / 14.8s（占节点 48%），因为要一次性吐出全部引用、且上下文从 2 条累积到 20 条（每轮重发）。
- 报告体积：agentic `extra_json` 15.4–15.6KB，fixed 4.1–4.5KB（多出的是 `agent_trace` 与 citations）。

### 6.4 HR 批量（HR 排序表图，并发 50）

| 批次 | 总墙钟 | 单份 wall avg / max | 吞吐 | 步/份 | tokens | 总成本 | 成本/份 | 成功 |
|---|---|---|---|---|---|---|---|---|
| 10 份 | **21.1s** | 15.8s / 19.6s | 28.4 份/min | 3 | 48,842 | ¥0.1825 | ¥0.0182 | 10/10 |
| 50 份 | **25.1s** | 19.2s / 23.5s | 119.5 份/min | 3 | 245,860 | ¥0.9145 | ¥0.0183 | 50/50 |
| **100 份** | **52.7s** | 16.9s / **33.7s** | **113.8 份/min** | 3 | 492,748 | **¥1.8351** | **¥0.0184** | **100/100** |
| 10 份 · full（对照） | **53.6s** | 41.1s / 52.4s | 11.2 份/min | 8 | 134,108 | ¥0.5258 | ¥0.0526 | 10/10 |

- **100 份 52.7s，满足「≤60s」，但余量仅 7.3s（12%）**；单份 wall p50 17.0 / p95 21.0 / max 33.7s，尾部排队明显。
- 并发 50 扩展性良好：10 份时单份 avg 15.8s，100 份时 16.9s —— **单份延迟几乎不随并发劣化**。
- 排序表图每份只 3 步（parse / jd_match / risk），成本只有完整流水线的 **35%**，吞吐高 10×。
- **full 对照**：完整流水线是筛选图的 2.54× 墙钟 / 2.88× 成本；按其单份 41.1s 外推 100 份（2 波）≈ **82s，超 60s 线** → **HR 批量必须走排序表图**，验证了 `HR_SCREENING=true` 的设计取舍。
- 100 份按节点：parse avg 6.44s（max 11.86）、risk avg 7.83s（max 20.93）、jd_match avg 5.66s（max 13.23）→ 关键路径 ≈ parse + max(jd_match, risk) ≈ 14.3s + 非契约开销 ≈ 2.6s。

### 6.5 并发扩展：4 份单份同时跑

| # | 样本 | 耗时 | 状态 | tokens | 成本 | overall |
|---|---|---|---|---|---|---|
| 1 | weak | 40.63s | done | 12,785 | ¥0.0512 | 60 |
| 2 | medium | 35.16s | done | 13,576 | ¥0.0539 | 85 |
| 3 | strong | 33.64s | done | 13,880 | ¥0.0509 | 91 |
| 4 | weak | 36.41s | done | 12,994 | ¥0.0530 | 60 |

- **总墙钟 40.63s，成功率 4/4，加速 3.80×**（串行 4×38.6=154.4s），单份延迟甚至略低于单独跑。
- tokens 合计 53,235，成本合计 ¥0.2090。
- **配置陷阱**：`LLM_MAX_CONCURRENCY=4` 的 `asyncio.Semaphore` 是**每个 GraphContext 各一份**（`workflow/graph.py:285-288`），不是全局限流。
  4 份并发时理论在飞请求可达 4×4=16；HR 批量 50 并发时更高。它的实际语义是「每份报告的并行度上限」，**想真限流必须换全局信号量**。

### 6.6 辅助基准

**RAG 检索（只读，32 条 query）**

| 指标 | p50 | p95 | mean | max |
|---|---|---|---|---|
| embed 单次 | 193.9ms | 579.2ms | 335.0ms | 2,888ms |
| search（含 embed） | 232.3ms | 504.2ms | 323.7ms | 1,946ms |
| 纯检索（扣 embed） | **48.2ms** | 294.5ms | 117.4ms | 1,742ms |

- 知识库 19 docs / **55 chunks**（mean 234 字/片）；embedding 占检索延迟 **约 83%**（p50 口径），Python 侧全表余弦只有 38–48ms。
  **在该规模下换 pgvector 几乎没有收益**——瓶颈是 embedding 网络往返，不是暴力扫描。
- embedding 单条 mean 258.5ms vs batch10 426.7ms → 摊到每条 **42.7ms，batch 快 6.06×**（`EMBEDDING_BATCH_SIZE=10` 的设计依据）。
- 抖动极大：embed p50 194ms / p95 579ms / 单次最长 2.89s，是 `retrieve` 节点抖动的独立来源。

**上传解析（生产入口 `extract_text`）**

| 类型 | 文件 | extract_method | 字符数 | mean |
|---|---|---|---|---|
| txt | weak.txt | text/utf-8 | 448 | **0.17ms** |
| docx | weak.docx | python-docx | 425 | **12.36ms** |
| pdf | weak.pdf | pypdf | 442 | **11.31ms** |
| pdf | strong.pdf | pypdf | 938 | **22.55ms** |
| 扫描 PDF | weak_scan.pdf | pypdf → OCR 缺失 | 0 | 2.25ms（降级） |
| PNG | weak.png | ocr-unavailable | 0 | 0.01ms（降级） |

> 解析相对 38.6s 可忽略（<0.1%）。扫描件/图片两行**只是「无引擎降级路径」**，不能当 OCR 吞吐上报；
> 宿主缺 `pdfplumber`，双栏/表格 PDF 版式重建耗时未测。

**SQLite 写微基准（300 次单条 INSERT+COMMIT）**

| 口径 | total | avg/行 | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| 单条 INSERT+COMMIT ×300 | 630.7ms | **2.102ms** | 2.065ms | 2.250ms | 2.610ms | 6.037ms |
| 300 行 / 单事务（对照） | 4.0ms | 0.013ms | 0.004ms | 0.005ms | — | 0.099ms |

- **单事务比逐条快 158×** → 成本几乎全部来自每事务一次 WAL fsync，不是 SQLite 本身。
- 一份完整报告约 20–30 次短事务 → **40–60ms，占端到端 0.1–0.2%**。SQLite 写不是瓶颈；且 100 份并发下 `busy_timeout=30s` 生效、零 `database is locked`。

### 6.7 全栈对照：PG + Redis + Celery（Env B）

**形态**：uvicorn API + Celery worker（4 并发）+ Redis 7 队列 + PostgreSQL 17，`LANGGRAPH_CHECKPOINT=postgres`，走真实 HTTP。
**时段**：15:23–16:08。**关键事实：15:28–15:55 供应商主层出现 504 与 ReadTimeout，Env B 的数字被这段事故污染。**

| 指标 | Env A（SQLite+inline） | Env B（全栈） | 说明 |
|---|---|---|---|
| 单份 fixed · 事故窗口 | 38.6s（15:00–15:15） | **147.1 / 148.3 / 169.2s** | 88 次 ReadTimeout、11 次 504，88 次降级到 `deepseek-flash` |
| 单份 fixed · 恢复后 clean（n=3） | 38.64s（n=5） | **35.90s**（34.06–37.79，stdev 1.87）/ 13,926 tok / ¥0.0521 | 与 Env A 同量级 |
| **交错对照（同时段 A/B/A/B）** | **A1 58.6s / A2 50.7s（mean 54.7）** | **B1 46.0s / B2 46.3s（mean 46.2）** | 同时段控制变量：**B 反而略快** |
| 单份 agentic（干净窗口） | 112.9s / 190,155 tok / ¥0.4498 | **103.1s / 168,254 tok / ¥0.4024** | 与 A 同量级（B 的 KB 更小，见下方口径） |
| 批量 10 份 | 21.1s | 83.3s（事故窗口） | 该时段 10 份 item wall mean 41.7s |
| 批量 50 份 | 25.1s | 148.3s（事故窗口） | item wall mean 112.0s，jd_match p95 59s |
| 批量 100 份 | **52.7s** | **83.9s**（恢复后，71.5 份/min，100/100，¥0.0202/份） | Env B 单份 wall mean 28.8s vs Env A 16.9s（供应商仍偏慢） |
| Celery 队列时延 | — | clean 单份 **145.6ms**；交错 134–139ms；批量 10 **1.71s**（冷 worker）、50/100 0.38/0.42s | 队列不是瓶颈 |
| 非 LLM 额外开销 | 1.58–2.20s/份（inline 冷启动摊入） | **0.45–0.63s/份**（worker 长驻摊薄） | 见下 |
| PG 单条 INSERT+COMMIT | SQLite **2.10ms** | **容器内 2.78ms**（应用真实路径）/ 宿主经端口映射 4.97ms（p95 5.32ms） | 每次 +0.7~2.9ms；一份报告约 20–30 次写 → +14–90ms（<0.3%） |
| checkpoint（PG） | 0（off） | **≈21.1MB / 170 任务 ≈124KB/份**（PG 库总 34.4MB） | 每 superstep 多一次 PG 写；需 TTL/清理 |
| 日志告警 | 0 error / 0 降级 | 97 次重试、88 次 ReadTimeout、11 次 504、2 次 embedding 429、1 次 degraded、0 Traceback、0 `database is locked`、0 HTTP 5xx | 全部集中在事故窗口 |

**结论（回答「换 PG + Redis + Celery 是否有性能提升」）**：

1. **单份速度：没有提升，也没有可测的额外开销。** 同时段交错对照 B mean 46.2s vs A 54.7s（B 略快，属噪声级）；恢复后 clean 35.9s 与 A 的 38.6s 同量级。队列仅 134–139ms。
2. **批量：本轮 Env B 的绝对数字更慢（100 份 83.9s vs 52.7s），但不能归因于 PG/Celery** —— 同期供应商单节点延迟仍偏高（item wall mean 28.8s vs 16.9s），且 50 份那次全程在 504/ReadTimeout 事故窗口内。**能归因于栈的固定成本只有 PG 每次写 +0.7~2.9ms 与 checkpoint ~21MB/170 任务。**
3. **换栈真正买到的是**：并发写不再被 SQLite 单写者锁死（历史 `database is locked` 消失）、Web 进程不被长任务占住、任务重启不丢、worker 可水平扩展、checkpoint 可断点续跑。
4. **代价**：多 0.13–1.7s 排队、PG 写 +0.7~2.9ms/次、checkpoint 表需要 TTL（当前 **21.1MB / 170 任务 ≈124KB/份**）。
5. **A/B 有两处非栈差异**（必须随结论引用）：① Env B 使用**全新 PG 卷**、只灌了 11 篇/16 切片知识库（Env A 是主库 19 篇/55 片，含 web 语料）→ B 的检索 prompt 更小，可能让 B 略快；② Env B 的 `LANGCHAIN_CHECKPOINT=postgres`（A 为 off）→ B 更慢一点。两者方向相反、量级都小于供应商抖动，因此本节的 A/B 差异只作**区间参考**，不构成「栈更快/更慢」的证明。
6. **一个必须带走的教训**：**同一台机器、同一天、相隔 1 小时，供应商抖动可以把「单份 35.9s」变成「154.9s」**。
   任何 A/B 对比若不在同时段交错执行，结论都可能是假的 —— 上一版报告把「批量慢 2–3 倍」归因于 429 抖动，本轮再次复现同类现象。

### 6.8 瓶颈拆解

| 排名 | 瓶颈 | 量级 | 证据 |
|---|---|---|---|
| 1 | **LLM 网络往返** | 单份 ~95% 时间、~99% 成本 | 关键路径 35.3s / 38.6s；SQLite+解析+检索合计 <0.3s |
| 2 | **输出长度（suggestion）** | 11.26s、¥0.0118（时长 29% / 成本 23%） | 输出 2,770 tok/次，其他节点 1.5–4.5× |
| 3 | **供应商抖动** | 单份 32.3–41.8s（stdev 3.31s）；ats 2.90×；RAG embed 0.15–2.89s | 同 prompt 同模型 |
| 4 | agentic evidence 的「最后一轮 finish」 | 16.1s / 14.8s（占 evidence 节点 48%） | 上下文累积 + 一次性吐全部引用 |
| 5 | SQLite / 解析 / 检索暴力扫描 | 分别 <0.2% / <0.1% / 48ms | 见 6.6 |

---

## 7. 价格与成本

### 7.1 计价口径：占位价其实是「忙时原价」

`.env` 的 `LLM_PRICE_INPUT_PER_M=2.0`、`LLM_PRICE_OUTPUT_PER_M=8.0` 曾被标注为「占位值」。
本轮核对官方定价页后确认：**这两个数字恰好等于阿里云百炼 `deepseek-v4.1-flash-1017`（华北2 北京）忙时、缓存未命中的原价**。

| 计费项（元 / 百万 token） | 闲时 | 忙时 | 缓存命中（闲/忙） |
|---|---|---|---|
| 百炼 `deepseek-v4.1-flash-1017` 输入 | 1 | **2** | 0.1 / 0.2 |
| 百炼 `deepseek-v4.1-flash-1017` 输出 | 4 | **8** | — |
| DeepSeek 官方 `deepseek-flash` 输入 | 1 | 2 | 0.02 / 0.04 |
| DeepSeek 官方 `deepseek-flash` 输出 | 4 | 8 | — |
| `text-embedding-v4` 文本输入 | 0.5 元 / 百万 tok（不分忙闲） | | |

> 来源：阿里云百炼模型页（2026-10-22 抓取，原文「错峰时段为东八区 22:00 至次日 8:00，其余时段为忙时」）、
> DeepSeek 官方定价页（2026-10-22 抓取，原文「空闲时段价格为高峰时段价格的一半」）。
> 详见 `findings/perf-2026-10-22/price/sources.md`。

**结论：看板成本是保守上限**。真实应付 ≈ 看板 × 0.5（闲时）~ 1.0（忙时），再减缓存折扣。
因此本报告所有「¥0.xx/份」都是**按忙时原价折算的上限**，不是账单；「单份 ≤0.5 元」的判定因此偏安全。

成本公式（`app/services/cost.py`）：

```
cost = tokens_in / 1_000_000 × price_in + tokens_out / 1_000_000 × price_out
```

### 7.2 单位成本（本轮实测 tokens）

| workload | tokens（本轮实测） | 账面（忙时原价） | 闲时原价 | 100 份（忙/闲） | 1000 份（忙/闲） |
|---|---|---|---|---|---|
| 单份 fixed | 13,149（5 次均值） | **¥0.0513** | ¥0.0257 | ¥5.13 / ¥2.57 | ¥51.3 / ¥25.7 |
| 单份 agentic | 190,155（2 次均值） | **¥0.4498** | ¥0.2249 | ¥44.98 / ¥22.49 | ¥449.8 / ¥224.9 |
| HR 批量每份 | 4,928（100 份实测） | **¥0.0184** | ¥0.0092 | ¥1.84 / ¥0.92 | ¥18.4 / ¥9.2 |
| agentic 最坏一次 | 200,575 | ¥0.4713 | ¥0.2357 | — | — |

- **「单份 ≤0.5 元」通过，但 agentic 余量只有 5.7%**（最坏一次 ¥0.4713/份）；fixed 余量约 10×。
- 数据库全量统计的 p50 口径与本次实测接近：fixed ¥0.0498（12,711 tok）、agentic ¥0.3134（130,986 tok）、HR ¥0.0187（4,963 tok）。
- embedding 目前**未计入成本看板**：按 `text-embedding-v4` 0.5 元/百万 tok、每份诊断 1 次检索（约 100–300 tok）估，
  约 **¥0.0001–0.0005/份**，可忽略但口径缺失。
- **不要引用看板的 `cost_per_report=0.0366`**：它的分母包含 69 份 mock/降级 0 成本报告与 HR 轻量报告，比单份 fixed 实测低 26.4%。

### 7.3 套餐单位经济（7 档）

| 套餐 | 价格 | 额度 | 该档 API 成本（忙/闲） | 毛利率（忙/闲） | 判定 |
|---|---|---|---|---|---|
| 免费体验 | 0 | 3 次 | ¥0.1494 / ¥0.0747 | —（获客成本） | ⚠️ 当前实际不限次 |
| 单份基础报告 | 9.9 | 1 次 | ¥0.0498 / ¥0.0249 | 99.5% / 99.7% | ✅ |
| 单份深度报告 | 19.9 | 1 次（agentic） | ¥0.3134 / ¥0.1567 | 98.4% / 99.2% | ✅ |
| 月度会员 | 39 | 不限次 | ¥9.40 / ¥4.70（按 30 份/月假设） | 75.9% / 87.9% | ✅ 回本 124 份（agentic） |
| 月度专业版 | 99 | 不限次 | ¥9.40 / ¥4.70（同上） | 90.5% / 95.3% | ✅ 回本 316 份 |
| HR 基础版 | 999 | 100 份/月 | ¥1.87 / ¥0.93 | 99.8% / 99.9% | ✅ |
| HR 专业版 | 2999 | 500 份/月 | ¥9.35 / ¥4.67 | 99.7% / 99.8% | ✅ |

**成本侧没有亏损套餐；风险全在「配额」不在「单价」**：

1. `QUOTA_ENFORCED=false` → 当前**免费=不限次**（库内已有 546 份带真实 LLM 成本的报告）。
2. `_has_paid_access()` 只看「有没有付过钱」→ 一旦开拦截，**9.9 元 = 永久不限次**。
3. 月度「不限次」没有任何公平使用上限（回本点 124/316 份/月，重度用户可击穿）。
4. HR 批量**不校验份数配额**（成本风险低，合同/履约风险高）。
5. 9.9 与 19.9 跑同一条 fixed 流水线，权益差异只存在于文案；「单份深度报告」的 agentic 能力并未被单独定价。

### 7.4 成本优化（按可落地性排序）

| # | 措施 | 预期降幅 | 风险/代价 | 是否建议 |
|---|---|---|---|---|
| 1 | 默认 `agentic` → `fixed` | **−84%（库内 p50）/ −89%（本轮实测）** | 丢失「结论级引用」卖点 | 视产品定位；不建议一刀切 |
| 2 | agentic 上下文压缩（只保留必要 findings / 摘要式 chunk） | −30~39%（估算） | 可能降低引用质量 | 建议先做小样本 A/B |
| 3 | fixed 输出瘦身（suggestion 输出限制） | −10~20%（估算） | 建议条数/长度可能下降 | 建议 |
| 4 | 前缀缓存命中（系统提示 + 知识片段） | −8~16%（估算） | 依赖供应商隐式缓存 | 建议观察命中率 |
| 5 | 批量 `file_sha256` 去重 | 重复上传直接归零 | 需新增去重表 | 建议（HR 场景刚需） |
| ❌ | 快模型路由（`LLM_FAST_MODEL`） | 实测**反效果**（输出膨胀 46%） | — | 不做 |
| ❌ | Batch 推理 5 折 | 该型号官方**不支持批量推理** | — | 不做 |
| ❌ | 关闭 checkpoint 省钱 | 与 token 成本无关 | — | 不做（无收益） |

### 7.5 价格章节的局限

1. **「公开价不可得」**：百炼的限时优惠、免费额度余量、本账号实际结算价均无法从公开页获得；
   主库 notes 里有 `403 Free quota exhausted` 痕迹，说明曾依赖免费额度。本报告只回答「按原价应付多少」（上限），不主张实付金额。
2. 未计入服务器/存储/带宽、支付通道费、人工客服、退款；支付通道费按 0.6% 量级假设时，9.9 元档约 ¥0.06，**比该档 API 成本还高**。
3. 「月度档 30 份/月」「保守系数 ×1.25」是假设值，已在文中标注为估算。
4. 看板 token 侧还有 4 处系统性漏记（截断/超时/坏 JSON 的已计费 token 被丢弃约 1–3%、agentic 强制收尾 cost 未累加、agentic 用全局价而 fixed 用供应商价、看板分母稀释），详见 `price/code-audit.md`。

---

## 8. 结论与验收对照

### 8.1 验收线对照

| 验收项 | 标准 | 本轮实测 | 判定 |
|---|---|---|---|
| 上传简历 + JD 后 60 秒内出报告 | ≤60s | Env A fixed **38.6s**（5 次均值，p95 41.8s）；Env B clean **35.9s** | ✅ 余量约 18–24s；事故窗口内会超时（供应商侧） |
| 单份报告 API 成本 | ≤¥0.5 | fixed **¥0.0513**（余量 10×）；agentic 最坏 **¥0.4713**（余量 5.7%） | ✅ 但 agentic 余量薄 |
| HR 100 份批量 | ≤60s | Env A **52.7s**，100/100 成功；Env B 干净窗口 **83.9s**（100/100，供应商仍偏慢） | ✅ Env A；⚠️ Env B 未达标，但差异指向供应商而非栈（见 §6.7） |
| 报告 ≥5 条可执行建议 | ≥5 | Env A 5 次报告均 8 条 findings | ✅（verify 全量见 §4.5） |
| 每条红色风险有可定位引用 | 100% | verify 断言 PASS；mock 一致性 20/20（span==quote）；Env A 报告 `knowledge_refs=3`/份 | ✅（B-4 已修复） |
| 同一简历两次诊断分数一致 | 稳定 | weak/medium/strong 跨 5 次 = 60/85/91 恒定 | ✅（risk_level 除外，见 §6.2） |
| 公平性通过率 100% | 100% | Env A 各次 `fairness_pass_rate=1.0` | ✅（verify 全量见 §4.5） |
| 生产可构建 | 可 build | **3/3 成功**（pnpm 11.28.5；`next build` 实际 Node v24.16.0，56–64s） | ✅ |

### 8.2 本轮最重要的 7 条结论

1. **性能瓶颈 100% 在 LLM 网络往返**：单份 38.6s 中关键路径 35.3s，SQLite/解析/检索合计 <0.3s。想快只有三条路：压输出、减节点、换更快的模型/区域；换数据库/队列无效。
2. **换 PG + Redis + Celery 不提升单份速度，也没有可测的额外延迟**（交错对照 B mean 46.2s vs A 54.7s；clean 35.9s）。它买的是并发安全、任务不丢、可扩展、可断点续跑，代价是队列 0.13–1.7s、PG 写 +0.7~2.9ms/份、checkpoint 约 21MB。
3. **HR 批量必须走 3 节点排序表图**：100 份 52.7s / ¥0.0184 每份；走完整流水线外推 100 份 ≈82s，不达标且贵 2.88×。
4. **agentic 是「能力增量」不是「性能选项」**：时间 2.92×、tokens 14.5×、成本 8.8×；换来 12–14 条结论级引用（全部通过 provenance 校验）。**它的 0.47 元/份已经接近 0.5 元验收线**，不能默认全量开启。
5. **成本看板是保守上限，不是账单**：占位价 2/8 恰为百炼忙时原价；真实应付 ≈ 0.5×（闲时）~1.0×（忙时）。7 档套餐成本侧全部正毛利，**风险在配额（免费不限次 / 9.9 买断 / 月付不限次 / HR 不校验份数）而不在单价**。
6. **上一版的最强检索结论已被数据推翻、但决策仍成立**：P9 的「BM25 在 k=8 +8.2pp 显著」在 v4 embedding 下变成 −3.1pp；但「不合入 BM25」依然正确，且产品口径（前 3 条）也变差 2.9pp。**换 embedding 必须重跑评测再决定任何检索参数。**
7. **安全仍是上线阻塞项**：A-1（管理后台鉴权）已修，但 `SMS_DEV_MODE=true` + `AUTH_SECRET` 空 + `_debug` 泄露账号三者叠加可伪造任意用户（含 admin）；支付回调无验签、知识库写接口开放、`/static` 可匿名直链简历。**这些与性能无关，但会一票否决上线。**

### 8.3 建议的下一步（按投入产出比）

| 优先级 | 事项 | 依据 |
|---|---|---|
| **P0 安全** | 生产配置关掉 `SMS_DEV_MODE`、强制 `AUTH_SECRET`、关闭 `/auth/_debug/*`；支付回调加验签；知识库写接口加 admin；`/static` 加归属校验或改签名 URL | §4.2 |
| **P0 稳定性** | 修复「agentic + 兜底 DeepSeek thinking 模型」400 崩溃（回传 `reasoning_content` 或避开 thinking 模式）；评测脚本加 per-case 容错 | §5.4.2 |
| **P0 成本护栏** | 打开 `QUOTA_ENFORCED` 并修 `_has_paid_access`（当前 9.9 元=永久不限次）；HR 批次校验份数配额；月度档加公平使用上限 | §7.3 |
| **P1 agentic 定价** | agentic 不应与 fixed 同价；单独定价或仅对 19.9 档开放；先做上下文压缩（估 −30~39%） | §6.3 / §7.4 |
| **P1 suggestion 瘦身** | suggestion 是时长（29%）与成本（23%）双料第一；限制输出条数/长度可同时降本提速 | §6.2 |
| **P2 可观测** | `LLM_MAX_CONCURRENCY` 改为全局信号量；为 504/ReadTimeout 建立告警；记录 p95 而非均值 | §6.5 / §6.7 |
| **P2 检索** | 默认 `top_k=5` 与 BM25 均不改；若要提升引用质量，优先补 gold/切片，而非调算法 | §5.2 / §5.5 |
| **P3 数据库** | 当前规模 SQLite 足够；需要多用户并发写时再上 PG（并配 checkpoint TTL 清理） | §6.7 |

---

## 9. 复现命令与数据索引

### 9.1 复现入口

```powershell
# 公共前置（Env A）
cd E:\ZhenResume\backend
$env:PYTHONUTF8="1"
$env:DATABASE_URL="sqlite+pysqlite:///E:/ZhenResume/findings/perf-2026-10-22/envA/perfA.db"
$env:UPLOAD_DIR="E:/ZhenResume/findings/perf-2026-10-22/envA/uploads"

# 单份 fixed / agentic
.\.venv\Scripts\python.exe -m scripts.bench_report --resume sample_resume_1_weak.txt
$env:WORKFLOW_MODE="agentic"; .\.venv\Scripts\python.exe -m scripts.bench_report --resume sample_resume_1_weak.txt

# HR 批量
.\.venv\Scripts\python.exe -m scripts.bench_batch --count 100

# 模型探针 / 检索 / 解析 / DB 写 / 并发
.\.venv\Scripts\python.exe -m scripts.probe_llm --embeddings
& .\.venv\Scripts\python.exe ..\findings\perf-2026-10-22\envA\scripts\rag_latency.py
& .\.venv\Scripts\python.exe ..\findings\perf-2026-10-22\envA\scripts\upload_parse.py
& .\.venv\Scripts\python.exe ..\findings\perf-2026-10-22\envA\scripts\db_write.py
& .\.venv\Scripts\python.exe ..\findings\perf-2026-10-22\envA\scripts\concurrency_4.py
```

```powershell
# 功能 / 契约 / 安全（隔离库）
cd E:\ZhenResume
pwsh -File findings/perf-2026-10-22/func/scripts/run_phase2.ps1   # verify + HTTP 黑盒
cd backend
python -m scripts.verify_auth
python -m scripts.check_report_consistency
```

```powershell
# 质量评测
cd E:\ZhenResume\backend
python -m scripts.eval_retrieval --top-k 5
python -m scripts.eval_retrieval --top-k 8
python -m scripts.eval_retrieval --top-k 10
python -m scripts.eval_topk_decision
python -m scripts.eval_sort --engine rule
python -m scripts.eval_sort --engine oracle
python -m scripts.eval_sort --engine semantic
python -m scripts.eval_rag
python -m scripts.smoke_agentic_rag
python -m scripts.smoke_agentic_report
```

> 注：`--engine semantic` 本轮未执行；`eval_rag` 在当前「兜底 DeepSeek thinking 模型」路径下有已知 400 崩溃（见 §5.4.2），复跑前需先修 fallback 或加 per-case 容错。

```powershell
# Env B 全栈（PG+Redis+Celery；用现有镜像 + 挂载当前代码）
cd E:\ZhenResume
docker compose -f findings/perf-2026-10-22/envB/docker-compose.envb.yml --env-file .env.local up -d db redis
docker compose -f findings/perf-2026-10-22/envB/docker-compose.envb.yml --env-file .env.local up -d api worker
# 基准脚本：findings/perf-2026-10-22/envB/scripts/（bench_http.py / bench_infra.py / export_pg.py / analyze_logs.py）
```

```powershell
# 价格复算
python findings/perf-2026-10-22/price/calc.py --from-db
```

### 9.2 原始数据索引

| 目录 | 内容 |
|---|---|
| `findings/perf-2026-10-22/envA/` | `env.md`、`summary.md`、`raw/`（5×fixed、2×agentic、4×批量、probe）、`rag_latency.json`、`upload_parse.json`、`db_write.json`、`concurrency_4.json`、`scripts/` |
| `findings/perf-2026-10-22/envB/` | `summary.md`、`metrics.json`、`raw/`（单份事故/clean、交错 A/B、批量、worker/api 日志、PG 导出）、`docker-compose.envb.yml`、`scripts/` |
| `findings/perf-2026-10-22/func/` | `summary.md`、`runbook.md`、`raw/`（P0 探针、契约探针、verify/verify_auth/HTTP 黑盒日志与 JSON） |
| `findings/perf-2026-10-22/eval/` | `summary.md`、`raw/`（retrieval/topk/sort/rag/smoke 日志与 JSON、旧缓存备份清单） |
| `findings/perf-2026-10-22/frontend/` | `summary.md`、`logs/`（typecheck/lint/build 日志）、`shots/`（9 张 1440/768/375 截图） |
| `findings/perf-2026-10-22/price/` | `summary.md`、`sources.md`、`code-audit.md`、`unit-economics.md`、`optimization.md`、`calc.py`、`calc-output.txt` |

---

## 10. 局限与诚实声明

1. **供应商抖动主导单次数字**：同一台机器、同一天，单份 fixed 从 **35.9s 到 169.2s** 都出现过（15:28–15:55 的 504/ReadTimeout 事故）。本报告用「交错 A/B + clean 复跑 + 时间窗标注」来控制，但**任何单次数字都不能当 SLA**。
2. **Env B 镜像不是从零构建**：本机到 pip 源约 160KB/s，全量重装会 20+ 分钟并干扰测试；因此用 2026-10-21 镜像 + 绑定挂载当前代码，并核对容器/宿主文件哈希一致。协议、队列、PG 行为不受影响，但「从零 build 可复现」未验证。
   另：Env B 使用全新 PG 卷（11 篇/16 切片知识库、checkpoint=postgres），与 Env A（19 篇/55 片、checkpoint=off）不是严格同条件，A/B 对比只作区间参考。
3. **宿主能力缺失**：无 OCR 引擎、无 `pdfplumber`、无 LibreOffice（`pdf_enabled=False`）。图片/扫描件只有降级路径数据；PDF 导出的 503 降级分支已验收，真实 PDF 保真未测。
4. **agentic 只有 2 个样本**；其成本倍数（8.8×）与时长倍数（2.92×）应看作区间而非点估计。
5. **成本是账面值，不是账单**：按官方**原价**（忙时 2/8、闲时 1/4）折算；限时优惠、免费额度余量、账号实付不可得。embedding 成本未入账（量级 0.0001–0.0005 元/份）。
6. **未覆盖**：多机/多用户混合负载、长时间稳定性、Celery 多 worker 横向扩展、PG 调优后复测、崩溃续跑演练、OCR 全链路、PDF 版式保真、真实支付渠道。
7. **测试数据隔离**：Env A 用主库副本 `perfA.db`，func 用 `func.db`，Env B 用全新 PG 卷；主库 `backend/data/zhenresume.db` 未被性能测试写入。测试期间产生的 `[bench]/[perf]` 数据保留在隔离库中，可随时删除。
8. **安全结论只反映当前运行配置**：`.env` 里 `AUTH_REQUIRED=false`、`SMS_DEV_MODE=true`、`AUTH_SECRET` 为空、`QUOTA_ENFORCED=false`。若生产用另一套配置，需按 §4.2 的探针重新验证。
9. **前端构建结论受 Node 版本影响**：系统 Node v24 + Next 14.2.15 是非官方支持组合 —— **上一轮（10-20）观察到 5 次 3 败的 flaky，本轮 3/3 通过**；生产 Docker 镜像内 Node 版本不同，结论不能直接外推。
10. **数字版本**：本报告所有数字绑定「HEAD `afd22e4` + 索引 text-embedding-v4 + 2026-10-22 知识库 19 docs/55 chunks」；换代码、换 embedding、重建索引后需重跑，不能跨版本引用。