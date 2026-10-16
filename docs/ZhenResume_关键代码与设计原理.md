# ZhenResume（简甄）关键代码与设计原理

> 说明：本文仅对 `backend/app` 现有源码做**阅读与归纳**，未修改任何代码。所有引用均来自仓库实际文件，便于开题/答辩时对照讲解。
> 检索日期：2026-10-13。涉及的核心文件：`app/workflow/engine.py`、`app/agents/*`、`app/services/*`、`app/api/*`。

---

## 0. 总览与全局设计铁律

ZhenResume 的本质是一条**"规则引擎打底 + LLM 纠错/润色 + 8 个 Agent 串行编排 + 最后质检"**的简历诊断流水线。它最反直觉、也最能体现设计功力的地方，不是"用了大模型"，而是下面三条铁律：

1. **评分不让 LLM 打分**（`analyze.py` 确定性公式）→ 同一份简历多次诊断分数一致，"改后对比分数变化"才不是假的。
2. **证据链不信任模型输出的位置**（`evidence.py` 服务端重新定位引文）→ 红色风险必须能在原文里点出来，否则强制降级。
3. **接口/契约/表结构照文档不动，只换实现**（MVP-lite）→ 每一项简化都是"可回退"的，升级是"换配置"而非"重写"。

其余模块都围绕这三铁律展开。下面按"功能 → 关键代码 → 设计原理"逐块拆解。

---

## 1. 文本抽取与 OCR —— `services/extract.py`

**关键代码**：`extract_text(data, filename)` 按扩展名分派；`_extract_pdf/_extract_docx/_extract_image/_extract_text`。

```python
def extract_text(data: bytes, filename: str) -> ExtractResult:
    ext = file_extension(filename)
    if ext == ".pdf":   return _extract_pdf(data)
    if ext == ".docx":  return _extract_docx(data)
    if ext in IMAGE_EXTENSIONS: return _extract_image(data)
    if ext in {".txt", ".md"}:  return _extract_text(data)
    return ExtractResult(text="", method="unsupported", note=f"不支持的文件类型：{ext}")
```

`_extract_image` 里有一段很写实的降级处理：

```python
try:
    import pytesseract
    from PIL import Image
except ImportError:
    return ExtractResult(text="", method="ocr-unavailable",
        note="未安装 pytesseract / tesseract，无法识别图片简历。请上传 PDF、DOCX 或直接粘贴文本。")
```

**设计原理**：
- **不抛异常，失败信息放在 `note` 里**：OCR 未装、扫描件无文本层，都不让整个任务崩，而是交上层走降级分支 —— 直接落地了 aaa.md §3.6 "支持失败重试与降级"。
- **OCR 可插拔且不强依赖**：识别依赖本机 `tesseract`（中文包 `chi_sim`），没装就明确告诉用户"请换 PDF/DOCX"，而不是静默产出一个空报告。
- **编码容错**：`_extract_text` 依次尝试 `utf-8 / utf-8-sig / gb18030 / latin-1`，覆盖 Windows 记事本、Mac 等多种来源的编码。

---

## 2. 规则解析（确定性基线）—— `services/parse_rules.py` + `agents/parse_agent.py`

**关键代码**（`parse_agent.py`）：规则先给出基线，LLM 纠错后以"规则兜底"合并，且 `critical=True`——解析失败整条链路无意义，直接让任务 `failed`。

```python
class ParseAgent(Agent):
    name = "parse"; title = "解析 Agent"; critical = True

    async def run(self, ctx, llm):
        baseline = parse_resume_text(ctx.resume_text)     # 确定性基线
        # ...
        parsed = _merge_parsed(baseline, result.data)      # LLM 为主、规则兜底
        ctx.parsed_resume = parsed
```

`_merge_parsed` 的核心是"**LLM 字段为空时回落到基线，避免模型越改越少**"，并且对漏掉的经历按 `(公司, 起始时间)` 去重补回：

```python
if baseline.work and len(merged.work) < len(baseline.work):
    known = {(w.company, w.start) for w in merged.work}
    for w in baseline.work:
        if (w.company, w.start) not in known:
            merged.work.append(w)
```

**设计原理**：
- **解析是确定性的、可复现的基线**，LLM 只做"纠错与补全"，且永远不能把规则已经拿到的信息弄丢。这是后面"分数可回归"的前提。
- **critical Agent 失败即终止**，因为后续 7 个节点都依赖 `parsed_resume`，没有输入就没有意义（区别于其他 Agent 失败只降级）。

---

## 3. Agent 基类与共享上下文 —— `agents/base.py`

**关键代码**：`AgentContext`（共享状态，逐层累积 `findings/scores/summary…`）与 `Agent` 抽象类（统一 `safe_run` 异常处理）。

```python
@dataclass
class AgentContext:
    task_id: str
    resume_text: str; jd_text: str
    parsed_resume: ParsedResume; parsed_jd: ParsedJd
    role_family: str = "general"
    knowledge_hits: list[dict] = field(default_factory=list)
    # 逐层累积的产出
    findings: list[dict] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    summary: str = ""; qc_report: dict = field(default_factory=dict)

class Agent(ABC):
    name: str = "agent"; critical: bool = False
    @abstractmethod
    async def run(self, ctx, llm) -> AgentOutput: ...
    async def safe_run(self, ctx, llm):
        try:
            return await self.run(ctx, llm)
        except Exception as exc:
            if self.critical: raise
            return AgentOutput(data={}, status="degraded", notes=f"{self.name} 执行失败，已降级：...")
```

**设计原理**：
- **统一契约**：每个 Agent 只做一件事，输入输出都是 JSON（可存 `workflow_steps` 表，独立可观测）；每个 Agent 自带 `mock_factory`，保证无 API Key 时链路完整。
- **异常分级**：非 critical 的失败降级为空产出、记录 `notes` 后继续，只有 `parse` 失败才终止 —— 单步异常不拖垮整条链路。

---

## 4. 8-Agent 工作流引擎 —— `workflow/engine.py`

**关键代码**：`run_workflow` 顺序跑 `AGENT_PIPELINE`，每步落一行 `workflow_steps`（status/tokens/cost/latency），并在 **qc 之后重算分数**。

```python
for index, agent_cls in enumerate(AGENT_PIPELINE, start=1):
    agent = agent_cls()
    step = WorkflowStep(task_id=task_id, step_name=agent.name, status="running",
                       input_json=_input_digest(agent.name, ctx))
    # ...
    output = await agent.safe_run(ctx, llm)
    step.status = "degraded" if output.status == "degraded" else "done"
    step.tokens = output.tokens; step.cost = output.cost; step.latency_ms = output.latency_ms
    # ...
    if agent.name == "qc":
        # QC 会改动 findings，必须在它之后再定稿分数，保证总分与问题清单一致
        ctx.scores = analyze.compute_scores(ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        report = _persist_report(db, task, ctx)
```

`_input_digest` 只存摘要、**不把简历全文写进库**（隐私 + 不膨胀）：

```python
def _input_digest(agent_name, ctx):
    return {"agent": agent_name, "resume_chars": len(ctx.resume_text),
            "work_count": len(ctx.parsed_resume.work), ...}  # 不存原文
```

**设计原理**：
- **手写状态机替代 LangGraph**：8 节点是确定线性链，无分支/循环/人机回环，LangGraph 的 checkpoint/可视化用不上；但节点签名 `async (state, llm) -> partial_state` 与 LangGraph 对齐，**升级只需替换本文件**。
- **QC 后重算分数**：质检会删/降发现项，若不重算，报告总分与展示的问题清单会自相矛盾（这是 README 明确点名的"容易忽略的设计决策"）。
- **全程可观测 + 隐私友好**：每步落 token/cost/latency；input 只存摘要，规避数据库膨胀与简历原文外泄。

---

## 5. LLM 适配层与三重降级链 —— `services/llm.py`

**关键代码**：`LLMClient.complete_json`：mock 模式直接走规则引擎；live 模式失败则指数退避重试，全部失败回落 `mock_factory` 并标记 `degraded`。

```python
async def complete_json(self, *, system, user, mock_factory, agent="unknown"):
    if self.mode == "mock":
        return LLMResult(data=mock_factory(), ... mode="mock", note="mock 模式：由规则引擎生成")
    last_error = None
    attempts = max(1, settings.llm_max_retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            return await self._call_deepseek(system=system, user=user, agent=agent)
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    # 全部重试失败 → 降级到规则引擎，保证任务不整体失败
    data = mock_factory()
    return LLMResult(data=data, ... mode="mock", degraded=True,
        note=f"LLM 调用失败，已降级为规则引擎：{type(last_error).__name__}: ...")
```

**设计原理**：
- **单一入口**：所有 Agent 只调 `complete_json`，不直接碰 HTTP；以后加 GPT-4o/Claude 只改这一层，Agent 零改动（OpenAI 兼容协议天然可换 base_url）。
- **零 Key 也能产出契约合规报告**：mock 模式就是规则引擎路径，开发与演示零成本。
- **降级不静默**：`workflow_steps.status = degraded` + `notes` 记录原因，管理后台可见。

---

## 6. 五维确定性评分与规则检测 —— `services/analyze.py`

**关键代码**：`compute_scores` 按 aaa.md 权重（ATS 30% / 影响力 25% / 清晰度 20% / 竞争力差距 15% / 风险 10%）合成总分，且**同一事实只扣一次**。

```python
def _penalty(findings, category, *, red, yellow, cap=2):
    """按严重度取该类别前 cap 条扣分，避免同一根因被多次惩罚。"""
    items = sorted((f for f in findings if f["category"] == category),
                   key=lambda f: SEVERITY_ORDER.get(f.get("severity","yellow"),1))
    return sum(weights.get(f.get("severity","yellow"), yellow) for f in items[:cap])

def compute_scores(findings, parsed_resume, parsed_jd, raw_text):
    ats_score      = _clamp(45 + coverage*55 - _penalty(findings,"ats",red=5,yellow=2,cap=2))
    impact_score   = _clamp(40 + quant_ratio*60 - _penalty(findings,"impact",red=5,yellow=2,cap=2))
    clarity_score  = _clamp(95 - _penalty(findings,"clarity",red=6,yellow=3,cap=2))
    match_score    = _clamp(45 + coverage*55 - _penalty(findings,"gap",red=7,yellow=3,cap=2))
    risk_score     = _clamp(100 - 32*red_risk - 22*yellow_risk - 5*green_risk)
    overall = _clamp(ats*0.30 + impact*0.25 + clarity*0.20 + match*0.15 + risk*0.10)
```

**设计原理**（README 记录的真实教训）：
- **分数对"报告条数"不敏感，只对简历本身质量敏感**：否则"改后对比分数变化"会是假的。因此分数线主要看**客观比率**（关键词覆盖率 `coverage`、量化比例 `quant_ratio`），发现项只做温和修正。
- **每个维度最多计 2 条扣分**：比如"3 条没量化"与"全篇没量化"是同一根因，原先每条都扣导致分数虚低失真，现在被修正。
- **风险维度不设上限**：任何未处理的风险都值得把安全度拉下来，呼应"重证据"的产品定位。
- 量化检测用 `QUANT_SIGNAL_RE` 量词表（含"单/笔/台/项/QPS"等），并剔除"技术栈：Java"这类标签行，避免把标签当成果。

---

## 7. 证据链（可追溯引文定位）—— `services/evidence.py`

**关键代码**：`locate_quote` 不依赖模型给的 offset，而是让模型只输出 `quote`，后端在原文里做**精确 → 折叠空白 → 最长可定位前缀**三级定位；定位不到返回 `None`。

```python
def locate_quote(raw_text, quote, *, min_prefix=8):
    idx = raw_text.find(quote)                  # 1) 精确匹配
    if idx >= 0: return [idx, idx+len(quote)]
    # 2) 折叠空白后匹配（模型把换行写成空格）
    # 3) 退化到最长可定位前缀（模型截断/润色了尾部）
    for cut in range(len(norm_quote), min_prefix-1, -1):
        nidx = norm_text.find(norm_quote[:cut])
        if nidx >= 0: return [start, end]
    return None
```

`build_evidence` 在 `span=None` 时打 `unverified=True`，交给质检处置。

**设计原理**：
- **不信任模型输出的字符区间**（模型数不准），让模型只输出引文文本，位置由后端精确定位 —— 这是"每条红色风险都能在原文点出来"这一契约的落地方式。
- **定位失败即"无证据"**：下游质检会把这类无原文依据的红色风险降级或剔除，从机制上杜绝"虚构证据"。

---

## 8. RAG 混合检索 —— `services/rag.py`

**关键代码**：`search` 打分 = `0.6 × 向量余弦 + 0.4 × 词面重合度`；`split_chunks` 按段落聚合定长切片并带重叠。

```python
vector_score = embeddings.cosine(query_vec, chunk.embedding)
lex_score    = embeddings.lexical_overlap(query, text)
score = 0.6 * max(0.0, vector_score) + 0.4 * lex_score
```

**设计原理**：
- **混合打分性价比更高**：纯向量对短查询（如"Kubernetes"）召回不稳，词面重合度补足召回；0.6/0.4 是工程权衡。
- **embedding 是本地确定性哈希向量（可升级点）**：MVP 不做语义向量，对"关键词式"岗位知识库够用；换 pgvector + bge-m3 时只需改 `embeddings.py` 一行，`search` 的 SQL 改成 `ORDER BY embedding <=> :q`。
- **一次检索全链路复用**：引擎在跑流水线前检索一次 `top_k=5`，避免每个 Agent 重复打库。

---

## 9. 建议收敛与"至少 5 条"保证 —— `agents/suggestion_agent.py`

**关键代码**：`_select_final` 先给**每个类别保底一条**，再按严重度补足（防止关键词类红色把"时间线空窗"挤掉，曾导致弱简历风险安全度虚高到 100）；`_ensure_minimum` 不足 5 条时先补**真实缺失项**，再补**真实优点（green）**，绝不编造问题。

```python
def _select_final(findings, limit=MAX_FINAL):
    # 1) 每个类别保底一条
    for item in ordered:
        if category in seen_categories: continue
        seen_categories.add(category); picked.append(item)
    # 2) 剩余名额按严重度补足
    for item in ordered:
        if len(picked) >= limit: break
        if id(item) not in picked_ids: picked.append(item)
```

```python
def _ensure_minimum(findings, ctx):
    if len(findings) >= MIN_FINDINGS: return findings
    for generator in (_supplementary_findings, _positive_findings):
        for supp in generator(ctx):   # 先补确实缺失项，再补 green 优点
            ...
    return ...
```

`_positive_findings` 里的每条都必须带原文证据 + "面试怎么用好它"的可执行建议，而不是"写得不错，继续加油"的废话。

**设计原理**：
- **诚实优先于凑数**：高质量简历本就只有 2 个问题，硬凑 5 个等于编造问题、伤信任；所以补的是"真实存在的优点"（`severity=green`），验收条数满足的同时不欺骗用户。
- **保底机制防维度失明**：避免单一类别（关键词缺失）把其他更严重但只有黄色的风险挤出报告。

---

## 10. 质检四道闸门 —— `agents/qc_agent.py`

**关键代码**：`_check` 逐条做 ① 歧视性内容一票否决 ② 红色风险必须有可定位引文 ③ 必须有可执行改写句 ④ 知识库引用（记录不拦截）；动作 `keep / downgrade / drop`，**降级比删除优先**。

```python
# ① 歧视性内容 —— 直接删
if DISCRIMINATION_RE.search(text_blob):
    return {"action": "drop", "rule": "discrimination", ...}
# ② 红色风险必须有可定位引文
if severity == "red":
    locatable = bool(quote) and (locate_quote(ctx.resume_text, quote) is not None
                 or locate_quote(ctx.jd_text, quote) is not None
                 or evidence.get("source") == "knowledge")
    if not locatable:
        return {"action": "downgrade", "rule": "no_evidence", ...}
# ③ 可执行建议
if not str(finding.get("rewritten_text") or "").strip() ...:
    return {"action": "drop", "rule": "not_actionable", ...}
```

**设计原理**：
- **最后一道闸门，且会改动 findings** → 因此引擎在 qc 之后**重算分数**（与第 4 节呼应）。
- **公平性硬约束**：歧视性内容一律 `drop`，配合 `risk_agent` 双层拦截，支撑"公平性检查通过率 100%"的验收指标。
- **降级优先于删除**：保留信息量，但绝不让"无证据的红色风险"出现在报告里。

---

## 11. 简历四格式渲染与编辑器闭环 —— `services/resume_render.py`

**关键代码**：`render_plain_text` 把编辑器内容渲染成纯文本存进 `raw_text`，喂给同一套 8-Agent 流水线；`render_docx` 刻意**不用表格/文本框/分栏**（那是 ATS 解析失败头号原因）；`docx_to_pdf` 由 DOCX 转出，版式与 DOCX 完全一致。

```python
def render_plain_text(content):
    # 章节标题用词表标准说法；经历条目日期落在行尾（解析器靠它判定新条目）；要点以 "- " 开头
    lines.append(f"{header}    {date}".rstrip() if date else header)
    lines.extend(f"- {h.strip()}" for h in item.highlights if h.strip())
```

`docx_to_pdf` 明确不做硬编码路径：

```python
if not settings.pdf_enabled:
    raise PdfUnavailable()   # 未配置 LibreOffice 时 503，前端引导改用 DOCX/浏览器打印
```

**设计原理**：
- **编辑器产物与上传简历"同一条链路"**：`POST /resumes/from-editor` 把结构化内容渲染成纯文本进入 `raw_text`，无需任何特殊分支，避免"编辑器简历诊断质量低于上传简历"。代价是必须保证渲染文本能被 `parse_rules.py` 正确读回（这也是 `verify.py` 有专门断言的原因）。
- **DOCX 单栏纯段落**：直接针对知识库里"ATS 关键词匹配的实际工作方式"做反脆弱设计。
- **PDF = DOCX 转出**：版式零偏差，且转换组件路径走环境变量，产品不硬编码运行环境。

---

## 12. 简历上传 / 导出 / 隐私删除 API —— `api/resumes.py`

**关键代码**：
- `create_resume`（契约 §2.1）：上传 → 落盘 → `extract_text` → 规则 `parse_resume_text` → 存 `raw_text + parsed_json`。
- `create_resume_from_editor`（契约 §2.3）：`raw_text = render_plain_text(content)`，并把编辑器原文塞进 `parsed_json["editor_content"]`（**不新增列，避免数据库迁移**）。
- `export_resume`：Markdown / DOCX / PDF 三格式，且**同步函数**（PDF 阻塞调用交给线程池，改 async 反而卡事件循环）。
- `delete_resume`（aaa.md §4 隐私要求）：删文件 + 级联删 `analysis_tasks / reports / findings`。

```python
# 编辑器内容塞在 parsed_json["editor_content"]，而不是新加一列：
parsed_json={**parsed.model_dump(), "editor_content": content.model_dump()}
```

**设计原理**：
- **零新增迁移**：利用 pydantic 忽略未知键的特性，把"这份简历是否由编辑器创建"用 `editor_content` 是否存在来判定，MVP 阶段不引入 Alembic。
- **隐私即功能**：删除走级联 + 物理删文件，落实"用户可删除数据、简历默认不用于训练"。

---

## 13. 前端编辑器与诊断闭环 —— `frontend/`（Next.js 14 App Router + TypeScript + Tailwind）

> 本节依据 README 与 `resume_templates.py`/`resume_render.py` 归纳，前端 TSX 未逐文件改动。

- **4 套内置模板**（`services/resume_templates.py`）：通用社招 / 应届 / 转行 / 技术岗强化，每套带区块骨架、写作提示与完整范例。提示内容提炼自同一知识库 → **编辑器建议与诊断报告建议不自相矛盾**。
- **结构化编辑器 `/editor`**：左侧按区块填写，右侧实时预览，支持条目增删、草稿自动保存、载入模板示例。
- **与诊断闭环打通**：编辑器一键"用这份简历发起诊断"（不走文件上传）；报告页"修改后重新诊断"跳回编辑器带回原内容，改完再测即对比分数变化。
- **直接拖入上传**：把现有简历拖进卡片即走既有 `POST /resumes` 接口，无新增接口。
- **视觉系统**：统一"Claude 风格"（暖米白底、珊瑚色强调、衬线标题、发丝边框），通过对 `tailwind.config.ts` 重映射内置色板实现，一处配置全局统一、回归风险极低。

---

## 14. 设计原理总览（贯穿全局）

| 设计决策 | 落地代码 | 为什么这么做 |
|---|---|---|
| 评分确定性、可回归 | `analyze.compute_scores` | 改后对比分数变化才有意义；LLM 打分会让同简历两次分数不同 |
| 证据链服务端定位 | `evidence.locate_quote` | 模型数不准 offset；红色风险必须能点回原文，否则降级 |
| 三重降级链 | `llm.complete_json` | 零 Key 也能跑通全链路；单点失败不拖垮整体 |
| 手写状态机替 LangGraph | `workflow/engine.py` | 8 节点是线性链，框架能力用不上；节点签名对齐，升级只换文件 |
| QC 后重算分数 | `engine.run_workflow` | 质检删/降发现项后，总分与清单才不自相矛盾 |
| 建议"诚实补优点" | `suggestion_agent._ensure_minimum` | 高质量简历不足 5 条时补真实优点（green），不编造问题 |
| 每个维度最多计 2 条扣分 | `analyze._penalty` | 同一根因不被多次惩罚，分数只对简历质量敏感 |
| 编辑器与上传同链路 | `resume_render.render_plain_text` | 避免两类来源诊断质量不一致，无需特殊分支 |
| 零新增 DB 迁移 | `resumes.py` 的 `editor_content` | 利用 pydantic 忽略未知键，MVP 不做 Alembic |
| 隐私即功能 | `delete_resume` 级联 | 落实"用户可删除数据" |
| 契约冻结 | `docs/api-contract.md` | 前后端并行开发唯一依据，verify 脚本 170+ 断言防漂移 |

---

*（本文档为源码阅读笔记，未对 `backend/app` 下任何文件做修改。）*
