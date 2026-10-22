# ZhenResume（简甄）V1.0 — MVP 代码骨架

> 60 秒诊断简历，让每段经历都被看见。
> 不是简历打分器，而是给出**风险标注、匹配分析、证据引用、可直接复制的改写建议**的工作流工具。

本仓库是 `aaa.md`（ZhenResume V1.0 启动文档包）的可运行实现，采用 **MVP-lite** 方案：
**模块边界、JSON 契约、表结构完全按文档落地，只把实现层换成更轻的等价物**，后续升级是"换配置"而不是"重写"。

---

## 一、60 秒跑起来

### 1. 后端

```powershell
cd E:\ZhenResume\backend

# 首次：创建虚拟环境并装依赖（需要 Python ≥ 3.10；已在 3.10.8 与 3.12.14 上验证）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 启动（首次启动会自动建表 + 灌入知识库种子数据）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

> 用哪个 Python 建 venv 都可以，但**建完就不要换**：venv 里装的是带 ABI 标记的二进制扩展
> （`pydantic_core`、`Pillow` 等），解释器版本与扩展 ABI 必须一致。见 [十一、运行环境与排错](#十一运行环境与排错)。

- API 根路径：<http://127.0.0.1:8000>
- 交互式文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

**不需要 API Key 也能跑通全链路**：未配置 `DEEPSEEK_API_KEY` 时后端进入 `mock` 模式，
报告由规则引擎生成（`llm_mode` 会在健康检查里显示为 `mock`）。配置 Key 后重启即切换真实模型：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY=sk-xxxx
```

### 2. 前端

```powershell
cd E:\ZhenResume\frontend
pnpm install
pnpm dev     # http://127.0.0.1:3000
```

前端读取 `NEXT_PUBLIC_API_BASE`（默认 `http://127.0.0.1:8000`），可用 `.env.local` 覆盖。

**手上还没有简历？** 直接打开 <http://127.0.0.1:3000/editor>，
挑一套版式（青简细线 / 深蓝序章 / 象牙书页 / 钴蓝横帆）按提示填，右侧实时预览，
可导出 Markdown / DOCX / PDF，也能一键发起诊断 —— 全程不需要先准备文件。
详见 [五、简历模板与编辑器](#五简历模板与编辑器v11-新增)。

> 导出 PDF 需要后端配置 LibreOffice 路径（见 `backend/.env.example` 的 `LIBREOFFICE_NODE` / `LIBREOFFICE_CLI`）。
> 未配置时 PDF 按钮会提示改用 DOCX 或浏览器打印，其余功能不受影响。

### 3. 一键自检

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.verify
```

这个脚本会用真实 HTTP 请求跑完整条业务链路（上传简历 → 解析 JD → 8-Agent 工作流 → 报告 →
下单支付 → 知识库检索 → HR 批量 → 隐私删除 → DOCX/PDF 抽取回归），并对 `docs/api-contract.md`
的每一项硬性要求做断言。它是"主链路真的能跑"的证明，不是单元测试的替代品。

想直接看报告长什么样，不用起服务：

```powershell
.\.venv\Scripts\python.exe -m scripts.inspect --all
```

### 4. 或者用 Docker（免装 Python / Node）

```bash
docker compose up -d --build
```

前端 <http://localhost:3000>、后端 <http://localhost:8000>、文档 <http://localhost:8000/docs>。
同样**不需要 API Key**（未配置即 mock 模式）。要接真实模型就
`copy .env.docker.example .env` 后填 `DEEPSEEK_API_KEY`。

容器内也跑得通端到端自检：

```bash
docker compose exec backend python -m scripts.verify
```

> 完整说明（数据卷、CORS、`NEXT_PUBLIC_API_BASE` 构建期内联的限制、PDF 导出、单独构建镜像）
> 见 [docs/docker-deploy.md](docs/docker-deploy.md)。

---

## 二、目录结构

```
ZhenResume/
├── aaa.md                        启动文档包（需求来源，未改动）
├── docker-compose.yml            一键起前后端（见 docs/docker-deploy.md）
├── .env.docker.example           compose 变量模板
├── docs/
│   ├── api-contract.md           ★ 冻结的 API 契约（前后端唯一接口依据）
│   ├── docker-deploy.md          用 Docker Compose 跑前后端
│   └── vm-deploy.md              在 Linux 虚拟机里运行（PyCharm SSH 远程解释器）
├── backend/
│   ├── app/
│   │   ├── main.py               应用入口（异常处理 / CORS / 静态文件 / 启动钩子）
│   │   ├── config.py             全部配置项（环境变量驱动）
│   │   ├── db.py                 引擎与会话（SQLite ⇄ PostgreSQL 切换点）
│   │   ├── models.py             ★ ORM，逐表对齐 aaa.md 的 DDL 草案
│   │   ├── schemas.py            请求体校验 + ParsedResume / ParsedJD
│   │   ├── serializers.py        响应序列化（严格照契约构造）
│   │   ├── errors.py             契约 §8 的统一错误结构
│   │   ├── agents/               ★ 8 个 Agent（每个一个文件，独立可观测）
│   │   │   ├── base.py           Agent 基类 + AgentContext 共享状态
│   │   │   ├── finding_utils.py  把 LLM 自由输出收敛成合规发现项
│   │   │   ├── parse_agent.py        解析（critical）
│   │   │   ├── jd_match_agent.py     JD 匹配与差距
│   │   │   ├── ats_agent.py          ATS 可解析性
│   │   │   ├── impact_agent.py       影响力 / 量化
│   │   │   ├── risk_agent.py         风险 / 合规
│   │   │   ├── suggestion_agent.py   建议汇总（保证 ≥5 条）
│   │   │   ├── report_agent.py       报告合成（评分在此定稿）
│   │   │   └── qc_agent.py           质检（最后一道闸门）
│   │   ├── workflow/engine.py    ★ 手写状态机（LangGraph 的替换点）
│   │   ├── services/
│   │   │   ├── llm.py            DeepSeek 适配层 + 三重降级链
│   │   │   ├── extract.py        PDF/DOCX/图片/文本抽取 + 可插拔 OCR
│   │   │   ├── parse_rules.py    规则解析（确定性基线）
│   │   │   ├── analyze.py        ★ 五维评分 + 全部规则检测
│   │   │   ├── evidence.py       ★ 证据链：引文定位回原文
│   │   │   ├── embeddings.py     本地确定性向量（embedding 升级点）
│   │   │   ├── rag.py            切片 / 入库 / 混合检索
│   │   │   ├── prompts.py        Prompt 注册表（可被后台覆盖）
│   │   │   ├── plans.py          套餐常量
│   │   │   ├── resume_templates.py 模板数据（4 套版式 + 4 套旧写作模板兼容）
│   │   │   ├── resume_render.py  ★ 纯文本 / Markdown / DOCX / PDF 四种渲染 + 双向转换
│   │   │   ├── cost.py           token 估算与成本核算
│   │   │   ├── report_render.py  诊断报告 Markdown 导出
│   │   │   └── seed.py           知识库种子数据
│   │   └── api/                  11 个路由模块，对应契约各章节
│   ├── samples/                  示例数据：3 份强弱不同的简历 + 1 份 JD（含 DOCX / PDF 版本）
│   ├── Dockerfile                后端镜像：python:3.12-slim，非 root，/app/data 数据卷
│   ├── .dockerignore             挡住 .venv（ABI 绑平台）与 data/（用户简历原文）
│   └── scripts/
│       ├── seed_knowledge.py     知识库灌数
│       ├── make_sample_docs.py   由 .txt 样本生成 DOCX / PDF 样本（保证二进制样本可复现）
│       ├── inspect.py            离线跑一份简历并打印完整报告（人工评估质量用）
│       ├── demo_improve_loop.py  ★ 演示「草稿版 → 改进版」分数对比（验证二次诊断卖点）
│       ├── verify.py             ★ 端到端契约验证（约 174–180 项，随环境浮动）
│       └── check_report_consistency.py  ★ 报告内部一致性（摘要总分 / 风险等级 / 证据 span）
└── frontend/                     Next.js 14 App Router + TypeScript + Tailwind
    ├── Dockerfile                多阶段构建，运行层只带 .next/standalone 产物
    └── .dockerignore             挡住 node_modules / .next
```

---

## 三、MVP-lite 相对文档的差异（以及为什么这样换）

设计铁律：**接口、契约、表结构照文档不动，只换实现**。因此每一项差异都是"可回退"的。

| 文档方案 | 本项目实现 | 为什么可以换 | 升级动作 |
|---|---|---|---|
| LangGraph 编排 | `workflow/engine.py` 手写状态机 | 8 节点是确定的线性链，无分支/循环/人机回环，LangGraph 的 checkpoint 与可视化用不上，却带来额外依赖与调试成本 | 只替换 `engine.py`，节点签名 `async (state, llm) -> partial_state` 已与其对齐 |
| PostgreSQL + pgvector | SQLite（DDL 同构）+ Python 侧暴力检索 | 表名、列名、字段类型一一对应；MVP 数据量下暴力检索完全够用 | 改 `DATABASE_URL`；`knowledge_chunks.embedding` 从 JSON 换成 `Vector(1536)` 并建 HNSW 索引 |
| Redis + Celery | FastAPI `BackgroundTasks` + 任务状态表 | 免去两个额外进程，本地一条命令起 | `workflow.launch_workflow` 换成 Celery task；引擎本身不变 |
| LlamaIndex | 自研切片 + 混合检索（0.6 向量 + 0.4 词面） | 检索逻辑透明可调；LlamaIndex 的价值在复杂索引与多数据源，MVP 用不上 | `services/rag.py` 换成 LlamaIndex retriever |
| 多模型路由 | DeepSeek 单供应商 + 适配层 | 适配层是 OpenAI 兼容协议，接 GPT-4o / Claude 只需加一个 provider 分支 | `services/llm.py` 加分支 |
| Langfuse + Sentry | `workflow_steps` 表记录 tokens / cost / latency | 数据模型文档里本来就有；看板从这张表算出来 | 在 `engine.py` 里加一个转发回调 |
| Clerk / Supabase Auth | `X-Device-Id` 请求头做匿名用户隔离 | MVP 不做登录，但配额与历史记录已挂在真实用户记录上 | 替换 `api/deps.py::get_current_user` |
| shadcn/ui CLI | 手写同风格组件（无 Radix 依赖） | 避免 CLI 交互与额外依赖 | 按需替换为官方组件 |
| 单份报告 PDF | Markdown 导出（浏览器可直接打印为 PDF） | 零依赖，内容与排版来源一致 | 接 Playwright / WeasyPrint，复用 `report_render.py` |
| 简历导出 PDF | DOCX → PDF，转换组件路径走环境变量 | 产品不该硬编码运行环境的安装位置；未配置时返回 `503 PDF_UNAVAILABLE` 并引导改用 DOCX / 浏览器打印 | 换成托管转换服务，只改 `resume_render.docx_to_pdf` |
| OCR 扫描件 | 可插拔 tesseract，未安装时明确降级 | 不强制用户装 tesseract；降级路径有明确提示而非静默失败 | 装 tesseract + `pytesseract`，代码自动启用 |

### 两个刻意"不简化"的地方

1. **评分不让 LLM 打分**。五维分全部由 `services/analyze.py` 的确定性公式算出。
   原因：同一份简历两次诊断必须给出同样的分数，否则"修改后重新诊断，对比分数变化"这个卖点就是假的。
   已有验证项 `同一简历两次诊断分数一致（可回归）`。
2. **证据链不信任模型输出的位置**。模型只输出引文，字符区间由 `services/evidence.py`
   在原文里重新定位；定位不到的红色风险会被质检强制降级为黄色。已验证项
   `每条红色风险都有可定位原文引用`。

---

## 四、核心链路

```
POST /api/v1/resumes   上传简历 → 文本抽取 → 规则解析 → 存 raw_text + parsed_json
POST /api/v1/jds       粘贴 JD  → 规则解析 → 技能/年限/学历/关键词
POST /api/v1/tasks     创建任务 → 202 立即返回 → BackgroundTasks 启动工作流
   │
   └─ 8-Agent 工作流（每步落一行 workflow_steps：status/tokens/cost/latency）
        parse      规则基线 + LLM 纠错 → ParsedResume
        jd_match   关键词覆盖 + 差距；LLM 剔除"表述差异"误报
        ats        关键词缺失 / 缺技能栏 / 表格线 / 篇幅
        impact     量化检测 + 强动词改写（缺数据用 [补充指标：…] 占位，不编数字）
        risk       时间线断层与重叠 / 技能矛盾 / 绝对化表述 / 合规敏感字段
        suggestion 合并去重 + 排序 + 保证 ≥5 条 + 生成 action_items
        report     五维定稿评分 + risk_level + summary + 知识库引用
        qc         ①②③④ 四项校验：证据 / 公平性 / 可执行 / 知识库引用
   │
GET /api/v1/tasks/{id}     轮询进度（8 步状态 + 耗时）
GET /api/v1/reports/{id}   结构化报告
```

**一个容易忽略的设计决策**：质检会剔除或降级发现项，所以定稿必须在 `qc` **之后**做。
但"在 qc 之后重算一次"这句话本身埋过一个坑 —— 见下表的最后四条：**报告内部的数字必须是同一次计算的结果**。

### 验证过程中发现并修复的真实缺陷

这些都是 `scripts/verify.py` 与 `scripts/inspect.py` 跑出来的，不是假设 —— 记录在此以免回归：

| 缺陷 | 后果 | 修复 |
|---|---|---|
| 要点正文含「2023 年双十一」被判定为"新经历条目头" | 凭空多出一段虚假经历，连带误报"经历没有内容要点"和"两段经历时间重叠" | 条目头判定加两道守卫：项目符号开头的行一定是要点；日期必须出现在行首或行尾（`_looks_like_entry_header`） |
| PDF 抽取把「项目经历」乱序成「目项 经历」 | 标题漏识别 → 整段项目经历串进工作经历 | 章节标题判定增加"字符多重集相同"的乱序兜底（`is_section_heading`） |
| PDF 项目符号落在私用区 `\uf0b7` | PDF 简历的要点识别不健壮 | 符号表补上私用区与常用符号（`BULLET_RE`） |
| 技能栏按空格切分，`Spring Cloud` 被拆成 `Spring`+`Cloud` | 误报"项目用了 Spring Cloud 但技能栏没写" | 技能抽取先按技术词表长词优先匹配（`extract_skills`）+ 判定改为双向子串（`analyze.risk_findings`） |
| 项目技术栈用"任意英文单词"正则抽取 | 把 `MTTR`/`QPS` 当技术名词，误报技能矛盾 | 只认已知技术词表 |
| `coerce_findings` 用 `original_text` 反推证据引文 | "JD 关键词缺失"的证据变成 JD 首行（岗位名），违背证据链原则 | 引文优先级改为 显式 quote > 已构造的 evidence.quote > original_text |
| 评分按发现项条数直接扣分 | 同一根因被多次惩罚（"3 条没量化"+"全篇没量化"），分数虚低失真 | 每个维度最多计 2 条；分数线以客观比率为主、发现项只做温和修正 |
| 最终 findings 纯按严重度取前 8 条 | 关键词类问题（多为红色）挤掉"时间线空窗"（黄色），弱简历风险安全度虚高到 100 分 | 先给每个维度保底一条，再按严重度补足（`suggestion_agent._select_final`） |
| 量化检测单位表缺 `单/笔/台/项` 等量词 | `每月 40 单降至 2 单` 这种真量化被当成没量化 | 补全量词表；`技术栈：…` 这类标签行不参与量化判断 |
| 用户已在简历里说明空窗期，报告仍要求「补一句说明」 | 让用户做已经做过的事，是最伤信任的一类建议（由 `demo_improve_loop` 发现） | 检测到空窗期说明时转为绿色正面条目「已主动说明，处理得当」，并附上那句原文作证据 |
| 高质量简历只出 2 条建议，达不到「≥5 条」的验收标准 | 硬凑 5 个问题等于编造问题 | 不足时补**正面发现项**（green，附原文证据 + 面试怎么用），见第五节 |
| 用 `<a href download>` 下载简历 / CSV | 浏览器原生导航**不带 `X-Device-Id`**，请求落到 anonymous 用户，导出直接 404（静态构建检查发现不了，只在真实浏览器里出现） | 三处下载统一改为 `fetch` + blob，并在 `api.ts` 里收口 device-id 逻辑；服务端补 `Access-Control-Expose-Headers: Content-Disposition`；契约 §0.1 写明这条约束 |
| **摘要里的总分与实际总分来自两次不同计算**（`report_agent` 用 `compute_scores(findings)`、落库用 `compute_scores_deterministic`） | 报告页同时出现 `overall=85` 和「综合得分 **84** 分」（`sample_resume_2_medium` 必现），核心数字自相矛盾 | 新增**唯一定稿入口** `analyze.finalize_report()`：分数 / risk_level / 摘要数字一次算完；`qc` 用它按**最终清单**定稿，两条引擎只落库不再重算。新增断言 `scripts.check_report_consistency` 守"摘要总分 == overall" |
| 风险等级只看 `category=="risk"` 的条目 | 「3 项高风险」旁边挂着黄色风险徽章；空简历 2 条 impact 红却 `risk=100/green` | `analyze.risk_level_from_findings()`：在契约派生的基础上吸收**全维度**红色（≥3 条红 → red；有红不得为 green）。HR 排序表同口径 |
| 空简历 / 扫描件 OCR 失败照样产出报告 | 一份没抽取到文本的简历拿到「综合得分 55 分、风险较低」（10 个边界输入 8 个 green）—— 给假结论比报错更伤信任 | `tasks.create_task` 增加可分析性闸门（`analyze_guard`）：文本过短或解析不出任何结构 → `400 RESUME_UNANALYZABLE` 并给出换格式的建议；HR 批量的失败条目也带上原因 |
| 证据 `span` 在字形折叠路径上按引文原长算 end | `raw_text[285:292]` 切出 `"Spring "` 而引文是 `"Spring Cloud"`，前端却判「已定位」 | `locate_quote` 立下不变式：返回区间必须满足 `text[start:end].strip() == quote`，做不到就返回 None（宁缺勿错）；折叠/去空格两级只认完整命中；`qc` 再按 source 严格复核红色条目 |
| 分享接口实现是 POST，契约写 GET；分享路由改 `/s/{token}` 后 verify 仍查 `/share/` | 照契约对接的客户端拿到 405；`verify` 的分享断言长期误报 | 路由改为 `GET|POST /reports/{id}/share`（生成链接是幂等读操作）；断言改为校验 `/s/{token}` 与有效期字段 |
| 模板示例缺 `avatar` 键，而 `EditorBasics` 有默认值 | 「editor_content 原样回显」永远不成立（契约 §2.3 要求原样回显） | 4 套模板的 `example_content.basics` 显式补 `avatar: ""` |

---

## 五、简历模板与编辑器（V1.1 新增）

求职者可以完全不打开 Word，直接在应用里写简历。

- **入口已恢复到主导航**：顶部「编辑简历」→ `/editor`（开始页：先决定模板与内容起点，两个都可以跳过），
  决策后进入真正的编辑器 `/editor/write`；首页 hero、账号中心「我的简历」、报告页也都提供了入口。
- **模板画廊（第一批 4 套版式）**：青简细线、深蓝序章、象牙书页、钴蓝横帆。
  模板库是 4 列卡片网格，带真实 A4 缩略图（用同一份 React 版式组件缩放渲染，不是截图），
  支持「简约 / 商务 / 清新 / 个性」风格、「社招 / 实习 / 校招」场景、「单栏 / 双栏」布局筛选，
  点卡片可看版式介绍、写作规则与区块结构。旧 4 套写作模板仍保留在 `TEMPLATE_MAP` 里，
  已保存简历按旧 `template_code` 打开时仍能取到对应设计，但不再出现在画廊列表。
- **版式 PDF 与 ATS DOCX 分开**：右侧预览支持 4 种真实版式；「导出版式 PDF（打印 / 另存为）」
  走浏览器打印，输出与网页预览 1:1 的 A4 PDF（页首色块、双线、衬线、背景色都保留）。
  「保存并导出 ATS 单栏 PDF / DOCX」仍是纯单栏文本版式，专门给招聘系统解析用。
  这是刻意取舍：ATS 最怕表格、文本框、分栏，版式只服务于人看的 PDF。
- **结构化编辑器**（`/editor/write`）：左侧按区块填写（个人信息 / 个人优势 / 工作 / 项目 / 教育 / 技能 / 证书 / 荣誉 / 自定义区块），
  右侧实时预览；支持条目增删、草稿自动保存、载入模板示例。顶部写作设置条可随时换模板 / 移除模板 / 改标题。
- **同一份简历可反复修改，不再重复新建**：编辑器保存时会先看有没有已保存的 `resume_id` ——
  有就 `PUT /api/v1/resumes/{id}` 原地更新，没有才创建。`savedResumeId` 会跟着草稿一起存在本机，
  刷新页面不会再每次导出一份、改一次多一份。模板选择（`template_code`）也会随简历落库，重新打开时自动恢复。
- **账号中心「我的简历」**：按登录账号列出已保存/上传的简历，可继续编辑或删除；删除会级联清掉它的诊断任务与报告。
  匿名期间在编辑器里建的简历，在同一个浏览器登录后会由后端自动迁移到账号名下（见 §十一「账号与管理员」）。
- **在开始页上传文档导入**：在 `/editor` 选择「上传文档」，把现有简历（PDF / DOCX / PNG / JPG / TXT）拖进上传区或点击选择，
  解析完自动进入编辑器并填入表单，**不必先绕到首页上传一次**。导入走的是既有接口 `POST /resumes`（契约 §2.1），
  因此没有新增任何接口。导入后会清空"已保存版本"标记 —— 上传件没有编辑器原文，导出只能以解析结果为源（有损）；
  清空后第一次导出或诊断会另存为一份带完整结构化内容的可编辑版本。
  也可以用「我的简历」挑回之前上传或保存过的简历；本机草稿会在开始页显式给出「继续编辑草稿」入口，不会被静默覆盖。
- **三格式导出**：Markdown（粘贴到网申表单）、DOCX（投递并微调）、PDF（由 DOCX 转出，版式与 DOCX 完全一致）。
  DOCX 刻意保持单栏纯段落 —— 不用表格、文本框、分栏，因为那正是 ATS 解析失败的头号原因。
  版式更丰富的 PDF 走浏览器打印分支，两者同名但用途不同（见上一条）。
- **与诊断闭环打通**：编辑器里一键「用这份简历发起诊断」（**不走文件上传**）；
  报告页的「修改后重新诊断」会跳回编辑器并带回原内容，改完再测即可对比分数变化。

### 一个容易设计错的点

编辑器产出的简历**不是另走一条链路**：`POST /resumes/from-editor` 把结构化内容渲染成纯文本存进 `raw_text`，
因此它与上传的简历在 8-Agent 流水线里完全等价，不需要任何特殊分支。

代价是必须保证一件事：**渲染出的纯文本要能被 `parse_rules.py` 正确读回**
（章节标题用词表的说法、经历条目的日期落在行尾、要点以 `- ` 开头）。
否则「编辑器写出来的简历」诊断质量会低于「上传的简历」。
`verify.py` 对此有专门断言（工作经历段数一致、项目经历未串段、技能被结构化）。

### 报告条数不足时补什么

`aaa.md §5` 要求报告至少 5 条建议。但一份各方面都达标的简历本来就只有 2 个问题，
硬凑 5 个等于**编造问题**。所以 `suggestion_agent._ensure_minimum` 的补充顺序是：

1. 先补**简历确实缺失的检查项**（缺联系方式、缺技能栏、经历没有要点……）；
2. 还不够就补**简历里真实存在的优点**，标记为 `severity=green`，附原文证据，
   并给出「面试时怎么用好它」的可执行建议（例如把量化成果展开成 STAR 结构、保持与 JD 术语一致）。

`severity` 已经说明它不是问题，验收条数也满足了 —— 这比编 5 个假问题诚实。

---

## 六、降级链（"支持失败重试与降级"的具体含义）

```
live 调用 DeepSeek
  ├─ 成功 ──────────────────────────────────→ 正常产出
  ├─ 失败 → 重试（默认 2 次，指数退避）
  │        └─ 仍失败 → 回落到该 Agent 的规则引擎实现，步骤标记 degraded
  └─ critical Agent（仅 parse）失败 ─────────→ 任务置 failed，前端提示重试
```

- mock 模式（无 Key）走的就是这条规则引擎路径，所以**零 Key 也能产出契约合规的完整报告**。
- 降级不会静默：`workflow_steps.status = degraded`、`notes` 记录原因，管理后台可见。
- 单步异常不影响整条链路的可用性，只有 `parse` 失败才终止任务（因为后续节点没有输入）。

---

## 七、验收标准对照（aaa.md §5）

| 验收项 | 实现位置 | 验证项 |
|---|---|---|
| 上传简历 + JD 后 60 秒内生成报告 | 8-Agent 串行 + BackgroundTasks | 看板 `avg_latency_ms`；mock 模式实测约 1 秒 |
| 报告含至少 5 条可执行改写建议 | `suggestion_agent._ensure_minimum`：先补缺失项，再补正面发现项 | `发现项 ≥ 5 条（验收标准）` + `高质量简历的报告同样满足「≥5 条建议」` |
| 每条红色风险有原文引用与证据 | `evidence.py` 定位 + `qc_agent._check` | `每条红色风险都有可定位原文引用` |
| 支付成功后报告可下载 | `billing.py` + `reports.py` Markdown 导出 | `支付回调返回 200` / `Markdown 导出返回 200` |
| 用户可删除数据 | `DELETE /resumes/{id}` 级联删除 + 物理删文件 | `删除简历返回 204` / `删除后返回 404` |
| 公平性检查通过率 100% | `risk_agent` + `qc_agent` 双层歧视性内容拦截 | `公平性通过率 100%` |
| 单份报告 API 成本 ≤ 0.5 元 | `cost.py` 按可配置单价核算 | `单份成本 ≤ 0.5 元` |

---

## 八、已知边界与下一步

**MVP 明确没做的**（诚实标注，不是遗漏）：

1. **登录体系已上线，但默认仍保留匿名设备通道**：`AUTH_REQUIRED=false` 时可用 `X-Device-Id` 匿名访问，可被伪造；
   生产环境应打开 `AUTH_REQUIRED=true`，并配置 `AUTH_SECRET`、关闭 `SMS_DEV_MODE`。
2. **支付是 Mock**：`pay_url` 指向假地址，回调不验签。接真实渠道时需补签名校验与对账。
3. **HR 批量是串行执行**：100 份简历会跑较久；且不校验 HR 套餐配额（配额闸门只在单份任务上）。
4. **图片简历 OCR 依赖本机 tesseract**：未安装时降级为"请上传 PDF/DOCX"，不会崩溃但也没有结果。
5. **规则解析对多栏排版简历会串行错位**：live 模式下由 LLM 纠错兜底，mock 模式下效果有限。
6. **embedding 是本地哈希向量**，不是语义向量：对"关键词式"岗位知识库够用，
   对长句语义检索会明显弱于 bge-m3 / text-embedding-3-small。
7. **mock 模式的关键词匹配是字面的，不做同义归并**：JD 写「性能优化」而简历写「优化慢查询」会被判为缺失。
   这是 live 模式下 LLM 负责修正的那部分能力（`jd_match_agent` 的 `false_gaps`），
   所以 mock 模式的关键词类发现项偏保守、宁可多报。
8. **没有 Alembic 迁移**：`init_db()` 直接建表，字段变更需手工处理。
   为此编辑器原文没有新增列，而是存在 `parsed_json.editor_content` 里（pydantic 会忽略未知键），
   这样「这份简历是否由编辑器创建」也能由该键是否存在来判定。
9. **PDF 导出需要配置 LibreOffice**：`.env` 里填 `LIBREOFFICE_NODE` 与 `LIBREOFFICE_CLI` 才可用；
   留空时返回 `503 PDF_UNAVAILABLE`，前端引导改用 DOCX 或浏览器打印。这是刻意设计 ——
   产品不应该硬编码运行环境的安装位置。
10. **上传简历的导出是有损的**：以解析结果为渲染来源，解析没识别到的内容不会出现在导出文件里。
    要无损往返（写什么、导出就是什么），请用编辑器创建简历。

**建议的下一步顺序**（延续 aaa.md 结尾的建议）：

1. **先验证质量再补工程**：用真实 Prompt 跑 10 份真实简历 + JD，人工评估报告可用性。
   `backend/samples/` 已放好 3 份强弱不同的样本，`scripts/verify.py` 可直接改造为批量评估脚本。
2. 接入真实认证与支付验签。
3. 换 PostgreSQL + pgvector，把 embedding 换成真实语义向量（这一步对检索质量提升最大）。
4. 引入 Redis + Celery，把 HR 批量改成并发 + 限流。
5. 接 Langfuse，把 `workflow_steps` 的数据转发出去做 LLM 可观测。

---

## 九、开发约定

- **改接口先改 `docs/api-contract.md`**：前后端并行开发全靠它，契约漂移的代价远大于写文档的成本。
- **新增 Agent**：在 `app/agents/` 加文件 → 在 `agents/__init__.py` 的 `AGENT_PIPELINE` 里按顺序注册 →
  在 `serializers.STEP_ORDER` 里同步步骤名 → 契约 §4.2 的步数声明要一起更新。
- **Prompt 改动**：代码里的默认值在 `services/prompts.py`；运行期覆盖走管理后台
  （`POST /api/v1/admin/prompts`，版本号自增，工作流下次执行即生效）。
- **报告字段**：只能通过 `serializers.serialize_report` 输出，不要在路由里手拼 dict。

```powershell
# 常用命令
.\.venv\Scripts\python.exe -m scripts.seed_knowledge --force   # 重建知识库
.\.venv\Scripts\python.exe -m scripts.verify                   # 端到端契约验证（未通过项应为空；总数随环境浮动约 174–180）
.\.venv\Scripts\python.exe -m scripts.check_report_consistency # 报告内部一致性（摘要总分/风险等级/证据 span/空简历闸门）
.\.venv\Scripts\python.exe -m scripts.demo_improve_loop        # 草稿版 → 改进版的分数对比
.\.venv\Scripts\python.exe -m scripts.inspect --all            # 离线跑全部样本并打印报告
.\.venv\Scripts\python.exe -m scripts.inspect --resume samples/sample_resume_1_weak.txt --category risk
.\.venv\Scripts\python.exe -m scripts.make_sample_docs         # 重新生成 DOCX / PDF 样本
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload    # 开发服务器
```

---

## 十、前端设计系统（Claude 风格）

前端视觉按 Claude / Anthropic 的观感统一改造：**暖米白背景、暖灰中性色、单一珊瑚色强调、衬线体标题、发丝边框取代重阴影**。

### 为什么"重映射"内置色板名，而不是逐个文件改类名

全站 20 多个文件里写的是 `slate-*` / `red-*` / `amber-*` / `green-*`。改造没有逐个改成 `ink-*`，
而是在 `tailwind.config.ts` 里**整体替换了这几个内置色板的色值**（改前已确认：全站只有 `badge.tsx` 的 `blue`
变体用了不可重映射的颜色族，且该变体从未被使用，已移除）。

这样做的好处：配色温度是全局属性，一处配置就能统一，改动可审阅、回归风险极低；
新代码即使顺手写 `slate-500`，落到的也是暖灰，不会意外引入冷色孤岛。
代价是"slate 不再是冷灰"这一反直觉之处 —— 所以配置里有显式声明，并额外提供语义化别名 `ink`。

一处配置同时还把 6 处 `shadow-sm` 的阴影换成了暖色调版本。

### 关键令牌

| 用途 | 值 | 说明 |
|---|---|---|
| 页面底色 | `ink-50` `#FAF9F5` | 米白，不是纯白 |
| 正文文字 | `ink-900` `#171612` | 暖近黑，不是 `#000` |
| 发丝边框 | `ink-200` `#E8E5DA` | 卡片的层级靠它表达，而不是靠阴影 |
| 强调色（文字/图标/焦点环） | `brand-500` `#D97757` | Claude 签名珊瑚色 |
| 强调色（按钮填充） | `brand-600` `#C4603F` | 用更深一档保证白字对比度 |
| 语义色 | 暖砖红 / 赭黄 / 苔绿 | 去饱和、偏暖，不在米白底上"喊叫" |
| 标题字体 | `Georgia` + `SimSun/Songti SC` | 拉丁走 Georgia 自带编辑气质，中文回落系统宋体 |
| 正文/界面字体 | 系统无衬线（PingFang / 雅黑） | 保证中文渲染稳定 |

### 顺带修掉的真实缺陷

| 缺陷 | 修复 |
|---|---|
| 雷达图轴标签被 SVG 边界裁掉（「抗风险 100」显示成「险 100」） | 把 `viewBox` 向外扩出标签所需边距 |
| 雷达图与坐标轴硬编码靛紫 `#4f46e5`、冷灰 `#e2e8f0`，绕过了主题 | 改用 `fill-brand-500/15`、`stroke-ink-200` 等主题类 |
| 定价页 7 张卡片挤在 3 列网格，第 7 张孤立在第三行 | 拆成「个人求职者 / HR 与团队」两组，各自排满 |
| 表头全大写（中文界面里生硬，也是典型"AI 默认"手法） | 改为句首大写 |
| 键盘用户缺少跳转到主内容的入口 | 布局里加 `.skip-link`（Tab 首个落点） |
| 按钮缺按下反馈、焦点环不统一 | 补 `active:scale-[0.98]`、统一 `:focus-visible` 焦点环、`ease-spring` 过渡 |

### 两个被实测否决的装饰方案（记录在此以免重复尝试）

想让页面有"纸感"，先后试了两种纯装饰方案，**都实测无效果，因此都没有保留**：

1. **SVG `feTurbulence` 颗粒层**：测空白区灰度标准差只有 `0.53`（等于不可见），
   却让整页截图从 106KB 涨到 2458KB；把不透明度调到能看见又会压到正文可读性。
2. **径向暖光晕**：定位用了超高 `body` 的百分比，实际整个光晕落在页面之外 ——
   顶部与底部亮度实测完全一致（249.00 vs 249.00）。

结论：本项目的层次感由暖色配色、衬线标题、发丝边框与留白承担，不叠加验证不出来的装饰层。

### 怎么自己复核视觉效果

仓库里带了一个无头浏览器截图工具（产物在 `.shots/`，已 gitignore）：

```powershell
# 需要前后端都在运行
pwsh -File .\.shots\shoot.ps1 -Prefix after -Pages "/,/editor,/pricing"
pwsh -File .\.shots\shoot.ps1 -Prefix after -Height 3000 -Pages "/report/<report_id>"
```

它优先用 Edge（本机 Chrome 启动会报"并行配置不正确"），每次用独立的临时 profile ——
复用同一 profile 时新实例会把请求移交给已运行的实例并立即退出，导致整批截不出图。
配套的 `.shots/crop.py` 可以裁切放大某块区域，用来核对字体渲染、间距这类细节。

---

## 十一、运行环境与排错

### 各运行时分别装在哪

| 用途 | 路径 | 版本 |
|---|---|---|
| 项目后端虚拟环境（**依赖装在这里**） | `E:\ZhenResume\backend\.venv` | 由下面的基座 Python 决定 |
| venv 的基座 Python | `D:\PythonVersion`（= PATH 上的 `python`） | 3.10.8 |
| 前端依赖 | `E:\ZhenResume\frontend\node_modules`（pnpm 的硬链接仓库在 `.pnpm`） | — |
| 运行期数据（SQLite、上传的简历） | `E:\ZhenResume\backend\data`（已 gitignore，含隐私数据） | — |
| 截图工具产物 | `E:\ZhenResume\.shots`（已 gitignore） | — |

本机还装着一套由 DSH 提供的运行时（Python 3.12.14 / Node / pnpm）。
它只在需要"一个开箱可用的 Python 或 Node"时使用，**项目依赖不装在它里面** ——
这样 DSH 升级或重装不会影响本项目。例如前端构建用的 Node/pnpm 就取自它：

```powershell
# 前端构建（避免 PATH 里的 node 版本不确定）
$node  = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
$pnpm  = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs"
& $node $pnpm build
```

### 账号与管理员

账号体系（`/api/v1/auth/*`）支持邮箱密码、手机验证码登录，token 存 `localStorage`。
`/api/v1/admin/*`（成本看板、线上 Prompt、全部反馈）**只允许 `users.role == "admin"`**，
匿名设备和普通登录都会返回 `403 ADMIN_REQUIRED`；前端 `/admin` 也会显示「无权访问」。

授权管理员（账号必须先登录/注册过一次）：

```powershell
# 本机 PyCharm / 命令行后端（SQLite）
cd E:\ZhenResume\backend
.\.venv\Scripts\python.exe -m scripts.grant_admin --email you@example.com
# Docker API（PostgreSQL）
docker exec zhenresume-api python -m scripts.grant_admin --email you@example.com

# 查看/撤销
docker exec zhenresume-api python -m scripts.grant_admin --list
docker exec zhenresume-api python -m scripts.grant_admin --email you@example.com --revoke
```

也可以在 `backend/.env` 配 `ADMIN_EMAILS=` / `ADMIN_PHONES=`（逗号分隔），命中的账号在登录/注册时自动提升。
⚠️ 当前注册流程**没有验证邮箱归属**，公网部署优先用手机号白名单或上面的脚本；同时记得配置 `AUTH_SECRET`、
关闭 `SMS_DEV_MODE`，否则后台鉴权修好了，短信验证码本身仍是公开的。

### 匿名数据迁移（登录后不丢简历）

编辑器允许未登录直接使用。登录/注册时前端会带 `X-Device-Id`，后端把该设备匿名用户名下的
简历 / JD / 分析任务 / HR 批次 / 反馈迁移到账号（`services/claim.py`，幂等；不动支付数据）。
因此「匿名改了一半 → 登录 → 简历消失」这个断点已经补上。

### 排错：uvicorn 报 `ImportError: cannot import name 'Self' from 'typing'`

**症状**：`python -m uvicorn` 启动时在 `uvicorn/protocols/websockets/...` 处抛
`ImportError: cannot import name 'Self' from 'typing'`；换个入口又会看到
`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`。

**原因**：venv 的解释器与已安装扩展的 ABI 不一致 —— 常见于 venv 的元数据被另一个 Python 版本
改写过（例如 `pyvenv.cfg` 的 `home` 指向了机器上的另一个 Python），于是出现
"3.10 解释器 + 3.12 的 `cp312` 二进制扩展"这种组合。`typing.Self` 需要 Python ≥ 3.11，
而 `_pydantic_core.cp312-win_amd64.pyd` 在 3.10 上根本无法加载。

**自查**（两条命令就能确认）：

```powershell
# 解释器版本 vs 扩展 ABI，两者必须匹配
& .\.venv\Scripts\python.exe -c "import sys;print(sys.version.split()[0])"
Get-ChildItem .\.venv\Lib\site-packages -Filter "*.pyd" -Recurse | Select-Object -First 5 Name
Get-Content .\.venv\pyvenv.cfg
```

**修复**：删掉 venv 重建（依赖会按解释器版本自动选兼容的 wheel，例如 3.10 上会装
`websockets 16.x` 而不是需要 3.11+ 的版本）：

```powershell
Remove-Item .\.venv -Recurse -Force
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m scripts.verify     # 以「未通过项为空」为准；总数随环境浮动约 174–180
```

**另一个可选加固**：本项目没有任何 WebSocket 端点，所以即使 `websockets` 包有问题，
也可以直接用 `--ws none` 启动，把这条可选依赖从关键路径上摘掉：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --ws none --port 8000
```
