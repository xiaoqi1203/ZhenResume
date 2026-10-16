# ZhenResume（简甄）代码拆分与生产化建议

> 文档目的：在不修改任何现有代码、不改动已冻结契约（API 契约 §*、Schema §7、DDL §*）的前提下，
> 对当前仓库做一次"可生产化拆分"体检：标出过胖的文件、指出耦合点，并给出
> **接口（contract / interface）与实现（implementation）分离** 的具体拆分方案与落地路线图。
> 本文是分析文档，不影响线上运行。

---

## 0. 当前代码体量快照

### 后端 `backend/app/`（源码合计约 7.7k 行）

| 文件 | 行数 | 角色 | 过胖风险 |
|------|------|------|----------|
| `services/analyze.py` | 704 | 五维评分 + 全部 finding 生成器 | 🔴 高 |
| `services/resume_render.py` | 575 | 纯文本/Markdown/DOCX/PDF 四种渲染 + 编辑器↔解析互转 | 🔴 高 |
| `services/resume_templates.py` | 552 | 内置模板（代码常量） | 🟠 中 |
| `services/parse_rules.py` | 537 | 规则解析全部区块 | 🔴 高 |
| `agents/suggestion_agent.py` | 447 | 建议筛选/补强/兜底 | 🟠 中 |
| `models.py` | 340 | 15 个 ORM 模型 | 🟠 中 |
| `services/seed.py` | 334 | 知识库种子灌入 | 🟡 低 |
| `schemas.py` | 267 | 全部请求/解析契约 | 🟠 中 |
| `serializers.py` | 245 | 全部响应序列化 | 🟠 中 |
| `api/resumes.py` | 237 | 简历资源路由 | 🟡 低 |
| `workflow/engine.py` | 235 | 串行工作流 + 重算 + 输入摘要 | 🟡 低 |
| `services/llm.py` | 195 | LLM 客户端 + 三级降级 | 🟠 中（缺接口抽象） |
| `api/hr.py` | 190 | HR 批处理路由 | 🟡 低 |
| `agents/qc_agent.py` | 172 | QC 四门校验 | 🟡 低 |
| `api/admin.py` | 153 | 管理后台路由 | 🟡 低 |

### 前端 `frontend/`（源码合计约 16k 行，业务代码约 3k+）

| 文件 | 行数 | 角色 | 过胖风险 |
|------|------|------|----------|
| `components/editor/editor-workspace.tsx` | 1939 | 编辑器工作区（上传/编辑/诊断/导出/预览全在一起） | 🔴 极高 |
| `lib/types.ts` | 680 | 全部 TS 类型（契约+域+UI） | 🟠 中 |
| `lib/api.ts` | 618 | 全部后端调用封装 | 🟠 中 |
| `app/hr/page.tsx` | 538 | HR 批处理页 | 🟠 中 |
| `lib/resume-content.ts` | 342 | 编辑器内容构造/转换 | 🟡 低 |
| `app/admin/page.tsx` | 333 | 管理页 | 🟡 低 |
| `app/page.tsx` | 315 | 落地页 | 🟡 低 |
| `app/pricing/page.tsx` | 308 | 定价页 | 🟡 低 |

**结论**：后端真正需要拆分的是 `analyze.py / resume_render.py / parse_rules.py` 三个 "上帝文件"，
以及 `schemas/serializers/models/llm` 的"全量单文件"模式；前端的核心痛点是 `editor-workspace.tsx`
单文件 1939 行、承载了整个编辑器交互。

---

## 1. 拆分总体原则（硬约束）

1. **契约冻结优先**：`docs/api-contract.md`、Schema §7、DDL §* 是项目铁律——
   任何拆分都只能"改文件落点、不改字段名与枚举值、不改表结构"。
2. **接口与实现分离（依赖倒置）**：对外暴露 `Protocol`/`ABC` 接口；
   具体算法（规则解析、LLM 厂商、渲染格式、检索后端）作为可替换实现。
3. **单一职责**：一个文件只回答一个问题；"算法选择"与"算法本身"拆开。
4. **注册表 / 工厂模式**：实现通过注册表挂载，工作流只依赖接口，不 import 具体实现。
5. **先补测试再拆**：评分公式、证据定位、`locate_quote` 是确定性回归核心，
   拆分前须先有单测，否则无法验证行为等价。

---

## 2. 后端拆分建议

### 2.1 `services/analyze.py`（704 行）— 拆成 `analyze/` 包

**现状**：单个文件内同时包含 `compute_scores`、五个 finding 生成器
（`ats_findings` / `impact_findings` / `clarity_findings` / `risk_findings` / `gap_findings`）、
时间线缺口、强弱动词表、量化信号正则。

**问题**：① 改动任一维度都要动大文件；② 五个生成器共享同一签名却无统一接口；
③ 评分权重与生成器混在一起。

**目标结构**：

```
services/analyze/
  __init__.py        # 对外只暴露 compute_scores / build_findings
  protocol.py        # FindingGenerator 协议 + Finding 数据类
  scoring.py         # 五维权重 + compute_scores（仅聚合，不写规则）
  ats.py             # ATS 维度 findings（实现 FindingGenerator）
  impact.py          # 影响力度量 findings
  clarity.py         # 清晰度 findings
  risk.py            # 风险 findings（含双向子串技能校验）
  gap.py             # 匹配缺口 findings
  heuristics.py      # QUANT_SIGNAL_RE / WEAK_VERBS / STRONG_VERBS / 时间线工具
```

**统一接口（接口层，冻结行为）**：

```python
class FindingGenerator(Protocol):
    dimension: str                       # "ats" | "impact" | "clarity" | "risk" | "gap"
    def generate(self, parsed, jd, raw_text) -> list[dict]: ...
```

工作流只调用 `analyze.build_findings(ctx)`，内部遍历已注册的生成器。
**评分公式不动**（铁律之一：评分不算给 LLM），只把"在哪算"从单文件挪到 `scoring.py`。

---

### 2.2 `services/resume_render.py`（575）+ `resume_templates.py`（552）— 拆成 `render/` 包

**现状**：`render_plain_text` / `render_markdown` / `render_docx` / `docx_to_pdf` 四套逻辑 + 编辑器↔解析互转
全在一个文件；模板是另一个独立常量文件，但二者强相关（渲染要用模板结构）。

**目标结构**：

```
services/render/
  __init__.py        # render(resume, fmt) 工厂分派
  renderer.py        # Renderer 抽象基类（接口）
  plain_text.py      # PlainTextRenderer
  markdown.py        # MarkdownRenderer
  docx.py            # DocxRenderer
  pdf.py             # docx_to_pdf 封装
  editor_convert.py  # to_parsed_resume / from_parsed_resume（编辑器与解析互转）
```

`resume_templates.py` 保留为 `render/templates.py` 或在 `services/templates.py` 单独成模块，
由渲染器只读引用（它本就是"产品定义常量"，不应和算法混放）。

**收益**：新增"导出 HTML / LaTeX"只需加一个 `HtmlRenderer`，不动其他文件；
PDF 依赖（docx2pdf / libreoffice）隔离在 `pdf.py`，便于在不同环境替换实现。

---

### 2.3 `services/parse_rules.py`（537）— 拆成 `parser/` 包

**现状**：一个文件用一套通用正则 + 一个 `SECTION_KEYWORDS` 字典串起所有区块解析
（basics/education/work/projects/skills/certificates/summary/others）。

**目标结构**：

```
services/parser/
  __init__.py        # parse(raw_text) -> ParsedResume（门面）
  protocol.py        # SectionParser 协议：parse(lines, ctx) -> (data, consumed)
  section_splitter.py# 章节切分（现有 SECTION_KEYWORDS 逻辑）
  basics.py / education.py / work.py / projects.py / skills.py / ...
  baseline.py        # 规则基线兜底（mock 模式实现）
```

`parse_agent.py` 在 live 模式下调用 `parse`（规则基线）后再让 LLM 补全，
接口不变——规则解析作为 `BaselineParser` 实现，未来可加 `LlmParser` 实现做 A/B。

---

### 2.4 `schemas.py`（267）+ `serializers.py`（245）— 拆成包，按域分组

**现状**：所有 Pydantic 模型（请求、ParsedResume、ParsedJd、编辑器内容）堆在一个文件；
所有响应序列化（报告/任务/HR/知识库）堆在另一个文件。

**推荐拆分**：

```
schemas/
  __init__.py
  requests.py     # 创建类请求体
  parsed.py       # ParsedResume / ParsedJd（契约 §7，冻结）
  editor.py       # EditorContent 及子项
  common.py       # 分页/错误等通用

serializers/
  __init__.py
  report.py       # 诊断报告响应
  resume.py       # 简历/任务响应
  hr.py           # HR 批处理响应
  knowledge.py    # 知识库响应
```

**注意**：`ParsedResume` / `ParsedJd` 字段名受契约冻结保护，拆分只换 import 路径，不改定义。

---

### 2.5 `models.py`（340）— 拆成 `models/` 包

15 个 ORM 模型（Resume、AnalysisTask、AnalysisReport、JobDescription、KnowledgeDoc、
HrBatch/HrBatchItem、Plan、Order、Feedback、PromptOverride、WorkflowStep 等）同处一文件。

**推荐**：按域分文件 `models/resume.py`、`models/analysis.py`、`models/hr.py`、
`models/knowledge.py`、`models/billing.py`、`models/admin.py`，`models/__init__.py` 统一再导出。
所有模型已对齐 `aaa.md` DDL，拆文件不改表结构。

---

### 2.6 `services/llm.py`（195）— 补"LLMProvider 接口"，实现可插拔

**现状**：`LLMClient.complete_json` 内置三级降级（live DeepSeek → retry → 规则引擎 mock），
但 DeepSeek 是直接硬编码的实现，换 OpenAI/Claude/本地模型要改这个类。

**推荐接口化**：

```python
class LLMProvider(Protocol):
    def complete(self, system, user, *, json_mode=False) -> str: ...
    mode: Literal["live","mock"]

class DeepSeekProvider: ...      # 现有实现
class OpenAIProvider: ...        # 未来实现
class MockRuleProvider: ...      # 现有规则兜底

def get_llm() -> LLMProvider:    # 工厂：按配置返回实现
```

降级链（live→retry→mock）保留在工厂/装饰器层，而非散落在具体 provider 里。
这符合项目"降级链"设计意图，同时让多厂商接入成为"加一个实现文件"的事。

---

### 2.7 `services/rag.py`（115）+ `services/embeddings.py`（95）— 抽 `Retriever` 接口

**现状**：`search` = 0.6×向量 + 0.4×词面；`embeddings` 是本地 blake2b 哈希向量。
二者紧耦合在本地实现，注释已说明"未来可换 pgvector"。

**推荐**：

```
services/retrieval/
  __init__.py          # search(query, k) 门面
  protocol.py          # Embedder + Retriever 协议
  local_embedder.py    # 现有哈希向量（默认实现）
  hybrid_retriever.py  # 0.6/0.4 混合（实现 Retriever）
  # 未来：pgvector_retriever.py
```

换向量后端时只替换 `local_embedder.py` 实现，混合检索与调用方代码零改动。

---

### 2.8 `api/*` 路由 — 已较清晰，建议补"依赖注入收敛层"

`resumes.py / hr.py / admin.py` 已按资源分文件，结构良好。
建议加 `api/deps.py` 统一注入 `db`、当前租户/用户、LLM 实例，
并在 `api/__init__.py` 注册统一异常处理器（把 `ApiError` 映射成契约 §0 的
`{ detail: { code, message } }`，目前分散在各路由里）。

---

## 3. 前端拆分建议

### 3.1 `components/editor/editor-workspace.tsx`（1939 行）— 最高优先级

**现状**：上传、编辑器、实时诊断、报告查看、导出、预览、区块增删全部塞在一个 `'use client'` 组件里，
既是容器又是视图又是状态机。

**推荐拆分**：

```
components/editor/
  editor-workspace.tsx     # 仅做布局 + 状态编排（大幅瘦身）
  use-editor-store.ts      # Zustand/Context 状态（已填区块、当前模板、诊断态）
  upload-dropzone.tsx      # 复用现有 upload-dropzone
  editor-canvas.tsx       # 区块编辑表单（basics/education/work...）
  section-block.tsx       # 单个区块的可增删条目
  diagnosis-panel.tsx     # 调 createTask + 轮询 + 展示 loading
  report-panel.tsx        # 复用 report-view.tsx
  export-menu.tsx         # 导出 DOCX/PDF/MD 触发
  preview-modal.tsx       # 复用 resume-preview.tsx
```

把"上传→编辑→诊断→导出"的流转抽成一个小型状态机（`use-editor-machine.ts`），
界面组件只负责渲染，逻辑下沉到 hooks，单测即可覆盖流转。

---

### 3.2 `lib/types.ts`（680）— 拆成 `types/` 子模块

**现状**：契约类型、域模型、UI 局部类型全在一个文件。

**推荐**：

```
lib/types/
  contract.ts     # 严格对齐 docs/api-contract.md 的线上契约类型（冻结区）
  domain.ts       # 简历/报告/HR 等业务域类型
  editor.ts       # 编辑器内容 EditorContent 及子项
  ui.ts           # 组件局部 props 类型
  index.ts        # 再导出
```

`contract.ts` 单独锁定，任何改动都要走契约评审（呼应后端冻结策略）。

---

### 3.3 `lib/api.ts`（618）— 拆成 `api/` 子模块

**现状**：所有后端调用在一个文件，按资源函数罗列。

**推荐**：

```
lib/api/
  client.ts        # 基础 fetch 封装 + ApiError 统一处理（现有逻辑）
  resumes.ts       # upload / create / get / list / delete / export
  tasks.ts         # createTask / getStatus / getReport
  jd.ts            # createJd / listJd
  hr.ts            # batch 相关
  admin.ts         # feedback / prompt / metrics
  knowledge.ts     # 知识库检索/上传
  index.ts         # 再导出
```

与后端 `api/*` 资源划分一一对应，前后端映射一目了然。

---

### 3.4 页面 `app/hr`、`app/admin`、`app/page`、`app/pricing` — 页面与组件分离

`hr/page.tsx`（538）、`admin/page.tsx`（333）内部仍含较多列表/表格/弹窗逻辑。
建议把表格、批量操作条、详情抽屉抽成 `components/hr/*`、`components/admin/*`，
页面文件只做数据获取 + 布局，降低单文件复杂度，也方便复用（HR 与管理后台的表格相似）。

---

## 4. 接口/实现分离的统一范式

贯穿全栈的可复用范式（项目已部分采用，建议显式固化）：

1. **Protocol/ABC 定义接口**（如 `FindingGenerator`、`Renderer`、`SectionParser`、
   `LLMProvider`、`Retriever`）。
2. **具体算法作为实现文件**，只依赖接口，不反向依赖调用方。
3. **注册表 / 工厂** 在模块 `__init__` 或 `deps` 层装配，工作流/页面只 import 接口与工厂。
4. **配置驱动选择**：`LLMProvider` 按 env 选、`Retriever` 按 env 选、`Renderer` 按导出格式选。

这正好放大项目已有的三条设计铁律：
- *评分不算给 LLM* → 评分实现可独立替换，接口不变；
- *证据链不信任模型定位* → `locate_quote` 作为独立可测实现；
- *契约/表不变，只换实现* → 本建议所有拆分都是"换落点不换契约"。

---

## 5. 分阶段落地路线图（零契约变更）

| 阶段 | 目标 | 关键动作 | 风险护栏 |
|------|------|----------|----------|
| **P0 补测试** | 锁住确定性逻辑 | 给 `compute_scores`、`locate_quote`、`parse_rules` 基线、混合检索加单测 | 无单测不拆，确保行为等价 |
| **P1 后端上帝文件** | `analyze / render / parser` 三包化 | 按 §2.1–2.3 建包，`__init__` 保留原公开符号 | import 路径兼容，跑通回归 |
| **P2 接口抽象** | `LLMProvider` / `Retriever` | 抽协议 + 工厂，DeepSeek 作为首个实现 | mock 降级链行为不变 |
| **P3 数据层** | `schemas/` `serializers/` `models/` 包化 | 按域分文件，`__init__` 再导出 | 字段/表结构零改动 |
| **P4 前端拆分** | `editor-workspace` 瘦身 + `types/api` 分包 | 抽 hooks/子组件，状态机化 | 视觉与交互零回归 |
| **P5 依赖注入** | `api/deps.py` + 统一异常 | 收敛 db/租户/异常映射 | 响应体格式不变 |

每个阶段结束后都应是"可独立部署、对外行为不变"的状态——这正是 MVP-lite 设计
（接口/契约/表冻结，只换实现）在生产化上的自然延伸。

---

## 6. 风险与约束提醒

- **不要为了拆而拆**：`engine.py`、`qc_agent.py`、`api/resumes.py` 体量适中且职责清晰，
  当前无需拆，强行拆反而增加间接层。
- **冻结区不可动**：`ParsedResume/ParsedJd` 字段、`api-contract.md` 定义的响应结构、
  `aaa.md` 的 DDL——任何拆分都只能改文件位置或加实现，不能改这些定义。
- **先测试后重构**：`analyze`、`evidence`、`parse_rules`、`rag` 是回归核心，
  没有单测覆盖前做拆分等于在无人看管的桥上换桥墩。
- **前端 1939 行是最大债**：它把交互、状态、网络、UI 全绑死，是后续所有功能迭代的瓶颈，
  优先级应高于后端那些"只是长、但内聚"的文件。

---

> 本建议文档为纯分析产出，未对 `backend/` 与 `frontend/` 任何源码做修改。
> 落地时请严格遵循 §1 硬约束与 §5 路线图，确保"可生产化"不以破坏已冻结契约为代价。
