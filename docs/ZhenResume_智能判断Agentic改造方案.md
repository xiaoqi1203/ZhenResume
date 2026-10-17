# ZhenResume 智能判断（Agentic）改造方案

> 状态：**待评估，未实施**。本文只描述方案与验收标准，不含任何对现有代码的修改。
> 定位：在保留「规则判断」既有流水线的前提下，新增一条**智能判断**链路，用真正的 Agent 封装 + Agentic RAG，让模型**自主决定调用哪些 tool、什么顺序、要不要补检索**。
> 关联文档：`aaa.md` §3.6/§五.3、`docs/ZhenResume_LangGraph接入与并发实现.md`、`.docs-source/agent-design.md`、`.docs-source/rag.md`

---

## 0. 一句话方案

新增 `backend/app/agent/` 包（5 个新文件），实现「LLM 原生 function calling + 有界工具循环 + Agentic RAG」，用 `WORKFLOW_MODE=fixed|agentic` 与前端一个开关切换；两条链路**共用**同一套 `services/*`（规则判定）与同一套落库/评分/渲染路径，因此前端展示结构、`analyze_reports` 契约、`workflow_steps` 8 步契约**全部不变**。

---

## 1. 已核实的可行性（实测，非推测）

| 前提 | 实测结果 | 结论 |
|---|---|---|
| 供应商支持原生 function calling | `aliyun/qwen3.8-max` 传 `tools` + `tool_choice=auto` → `finish_reason=tool_calls`，一次返回 **2 个并行 tool call** | ✅ 不需要换模型、不需要新依赖 |
| 真实语义 embedding 已启用 | `EMBEDDING_PROVIDER=http`、`qwen3.7-text-embedding-flash`、1024 维；库内 16 chunk 全部带 `embedding_model` 元数据；含 Ollama 回退层 | ✅ 语义检索已在跑，AI 模式直接复用 |
| 检索质量基线（12 条标注 query） | 纯向量 recall@3 **100%** / MRR **0.861**；当前混合 0.6/0.4 同为 100% / 0.861；纯词面 92% / 0.819 | ⚠️ **排序已无多少空间** → agentic 的价值必须落在「检索决策」与「引用可追溯」，不能声称赢在排序 |
| LLM 降级链 | `aliyun:qwen3.8-max → deepseek-v4-pro-0813 → qwen3.7-flash → … → deepseek:deepseek-flash` | ✅ AI 模式复用同一套链与计价 |

> 由此确定一个**设计纪律**：智能判断不允许以「检索更准」作为主要卖点，它的验收指标是 **自主工具序列 + 多轮检索 + chunk 级可验证引用 + 自适应覆盖**。

---

## 2. 双模式切换设计

### 2.1 开关层级（三处，逐层可覆盖）

| 层级 | 字段 | 默认 | 说明 |
|---|---|---|---|
| 全局配置 | `WORKFLOW_MODE = fixed \| agentic` | `fixed` | 默认与现状完全一致；未配置任何东西时行为零变化 |
| API 请求 | `POST /api/v1/tasks` 请求体新增 `mode?: "fixed" \| "agentic"` | 跟随全局 | 前端开关直接传这个字段 |
| 任务持久化 | `analysis_tasks.extra_json.mode` | — | 任务落库时记录当次用哪条链路，**可审计、可对账、可回归** |

> 注意与现有 `WORKFLOW_ENGINE`（`legacy|langgraph`）区分：那个是**谁驱动编排**（手写状态机 / LangGraph），本方案的 `WORKFLOW_MODE` 是**节点内部做什么判断**（规则 / 智能）。两者正交，互不覆盖。

### 2.2 前端

`frontend` 上传/诊断入口加一个二选一分段控件：

```
判断方式  [ 规则判断 ]  [ 智能判断 ]
           稳定可复现       自主分析（Agent）
```

- 选择结果写进 `POST /tasks` 的 `mode`；
- 结果页新增「分析过程」面板（仅智能模式有数据）：展示工具调用时间线、每轮检索 query、引用来源，即 Agent 的 trace 可视化——**这就是给评审/面试看的 agentic 证据**。

### 2.3 不变式（硬约束，写进测试）

1. `mode=fixed` 时，**行为与改动前逐字节等价**：现有 `verify.py` 全绿、8 步 `workflow_steps`、同一简历两次诊断分数逐位相同。
2. 两条链路最终都调用 `analyze.compute_scores_deterministic` 定稿分数 → **同一份简历在两种模式下分数一致**（若不一致即为 bug，因为分数是规则算的）。
3. 两条链路都产出同一个 `findings` schema（`finding_utils.py:90-99`）→ 前端、`report_render`、导出、HR 排序表无需任何改动。
4. 所有引文仍由 `services/evidence.locate_quote` 复核落点，**不信任模型给出的 offset**（沿用现有原则）。

---

## 3. Agent 封装设计

### 3.1 包结构（全部为新增文件）

```
backend/app/agent/
├── __init__.py          # 导出 AgentOrchestrator / TOOLS / run_agentic
├── tools.py             # 工具注册表：6 个 tool 的 JSON Schema + 执行器
├── orchestrator.py      # Agent 核心：有界工具循环 + 收敛 + 产出 trace
├── trace.py             # AgentTrace 记录器（可观测性）
└── prompts.py           # Agent 的 system prompt（含铁律与停止条件）
```

**与现有 `agents/` 的关系：零修改、零导入反转。** `agent/` 单向依赖 `services/*` 与 `agents/finding_utils`，绝不反向依赖；`agents/` 8 个文件一行不动。

### 3.2 工具清单（`tools.py`）

工具**只封装已有的确定性能力**，零新业务逻辑——这是「Agent 编排既有能力」而非「重写一套」的关键。

| tool | 底层实现（已存在） | 参数 | 返回 |
|---|---|---|---|
| `parse_resume` | `services/parse_rules.parse_resume_text` | `{}`（读任务内简历） | 结构化 `ParsedResume` + 计数 |
| `jd_match_check` | `analyze.missing_keywords` + `analyze.gap_findings` | `{include_bonus_skills?: bool}` | 覆盖率、缺失/已覆盖关键词、gap findings |
| `ats_check` | `analyze.ats_findings` | `{focus?: "keywords"\|"format"\|"length"\|"all"}` | ATS findings |
| `impact_check` | `analyze.impact_findings` + `content_bullets` | `{section?: "work"\|"projects"\|"all"}` | 量化比率、弱动词、影响类 findings |
| `risk_check` | `analyze.risk_findings` + `timeline_gaps` | `{scope?: "timeline"\|"contradiction"\|"compliance"\|"all"}` | 风险 findings、空窗月数 |
| `kb_search` | `services/rag.search` | `{query: str, scope?: enum, role_family?: str, top_k?: int}` | hits（含 `chunk_id`/`doc_id`/`score`/`text`） |
| `read_chunk` | `models.KnowledgeChunk` 直读 | `{chunk_id: str}` | 单个切片全文（用于「摘要不够，看全文」） |
| `finish` | — | `{findings, action_items, summary}` | 结束循环并提交结论 |

设计要点：

- **参数是模型自由填的**，不是代码写死的。例如 `ats_check(focus="format")` vs `ats_check(focus="keywords")` 的选择由模型决定；`kb_search` 的 `query` 由模型自拟（这是 Agentic RAG 与 `rag.build_query` 固定拼接的本质区别）。
- `kb_search` **新增 `role_family` 参数**，把现有「死代码」的岗位族过滤能力真正接线（`models.py:211` 有索引、`rag.search` 返回该字段但从未使用）。
- 每个 tool 执行器都返回**结构化 + 可序列化的结果**，并记录 `args_digest` / `result_digest` 进 trace。
- `finish` 是显式终止工具：让「停止」也变成模型的一个决策，而不是靠解析输出格式猜。

### 3.3 Agent 循环（`orchestrator.py`）

```
state = 本次任务的只读上下文（resume_text / jd_text / parsed_* / role_family）
trace = []
messages = [system_prompt] + [user_goal(resume, jd)]
for step in 1..max_steps:                     # 默认 12
    resp = llm.complete_with_tools(messages, TOOLS)   # 原生 function calling
    trace.record_assistant(resp)
    if resp.tool_calls 为空: 强制要求调用 finish（一次），否则降级兜底
    for call in resp.tool_calls:              # 支持并行调用，按序执行
        result = execute_tool(call.name, call.arguments)   # 引擎执行，模型不自执行
        trace.record_tool(call, result)
        messages.append(tool_result_message(call.id, result))
    if 到达预算上限: break
final = finish 参数 → 经 finding_utils.coerce_findings 规整 → evidence 重新定位
```

关键设计（每一条都直接对应「agent 的四要素」）：

| 要素 | 实现方式 |
|---|---|
| 自主决策 | 工具选择、顺序、并行、参数**全部由模型输出**；Python 侧只有一个 `for` 循环和工具执行器，**没有 if-else 决定"下一步做什么"** |
| 工具使用 | 原生 `tools` + `tool_choice=auto`，实测可用 |
| 迭代 | 循环持续到模型调用 `finish`，或触发预算上限（`max_steps` / `max_tool_calls` / `wall_clock_s`） |
| 重规划 | 工具结果回灌进 messages，模型可据此改变后续策略（如：`risk_check` 发现空窗 → 追加 `kb_search("空窗期怎么解释")`） |

### 3.4 Agentic RAG 的具体形态

与现状（`workflow/nodes.py:64-70`：进图前检索一次、`top_k=5`、全链路复用、只注入前 3 条 × 300 字）的区别：

1. **检索是工具，不是前置步骤**：模型自己决定何时检索、检索几次、用什么 query。可以不检索（如 JD 信息已足够时）。
2. **query 由模型自拟，不是模板拼接**：模型可针对不同子问题发多个 query（「后端工程师量化标准」「空窗期解释范例」）。
3. **支持多轮**：拿到首轮结果后，模型可判断"依据不足"并再次 `kb_search`（上限 `max_retrieval_rounds=3`，由 trace 统计）。
4. **支持下钻**：`kb_search` 只给摘要 → 模型可用 `read_chunk(chunk_id)` 取全文，再决定是否引用。
5. **引用是 chunk 级的、可校验的**：`finish` 的每条 finding 必须带 `citations: [{chunk_id, quote}]`；后端用 `locate_quote` 在**该 chunk 文本**内定位，定位失败 → 该引用作废并计入 `citations_failed`。报告里保留现有 `knowledge_refs` 结构（前端不用改），但新增发现项级 `knowledge` 证据（`finding_utils.resolve_evidence` 已有 `source="knowledge"` 分支，目前几乎用不上）。

### 3.5 停止条件与预算（防跑飞）

| 预算 | 默认值 | 超限行为 |
|---|---|---|
| `max_steps` | 12 | 强制进入收尾：最后一次请求带 `tool_choice={"type":"function","function":{"name":"finish"}}` |
| `max_tool_calls` | 20 | 同上 |
| `max_retrieval_rounds` | 3 | 后续 `kb_search` 调用直接返回"已达检索上限"，迫使模型收尾 |
| `wall_clock_s` | 90 | 同上，并把 `truncated=true` 写进 trace |
| `est_cost_cny` | 0.6 | 超预算立即收尾（对应 `aaa.md` 单份 ≤0.5 元要求，留余量） |

**已实测算得**：现有 8 节点串行 live 约 **107–130s**、8 次 LLM 调用。Agent 循环的期望是 **5–8 次调用**（部分工具是本地规则、无 LLM 成本），因此 90s 预算不激进。

### 3.6 降级链（继承现有原则，绝不静默）

| 情况 | 行为 |
|---|---|
| 供应商不支持 function calling | 自动切 **JSON 协议回退**：要求模型输出 `{"action": tool, "arguments": {...}}`，由引擎解析并执行；trace 标记 `protocol="json"` |
| 模型连续 2 次不返回工具调用且不 finish | 追加一次强约束提示；仍失败 → 走规则兜底 |
| 工具执行异常 | 结果以 `{"error": "..."}` 回灌给模型，**让模型自己决定重试或换路**（这是 agentic 的正确降级方式，不是直接失败） |
| LLM 全链失败 | 复用现有 `mock_factory` 机制，产出规则结果，`status="degraded"` 并写 notes |
| 预算超限 | 用已有 findings 收尾，`trace.truncated=true`，报告标注"分析提前收敛" |

---

## 4. 数据与可观测性

### 4.1 trace 结构（新增，落 `workflow_steps.output_json`，不新建表）

```json
{
  "mode": "agentic",
  "steps": 6,
  "tool_calls": 8,
  "retrieval_rounds": 2,
  "self_corrections": 1,
  "citations_verified": 7,
  "citations_failed": 1,
  "truncated": false,
  "latency_ms": 41320,
  "tokens": 12844,
  "cost": 0.0812,
  "timeline": [
    {"step": 1, "tool": "parse_resume", "args": {}, "latency_ms": 8, "result_digest": "work=3 project=2 skills=11"},
    {"step": 1, "tool": "kb_search",   "args": {"query": "后端工程师 量化标准", "top_k": 5}, "latency_ms": 420, "result_digest": "5 hits, top=0.542"},
    {"step": 2, "tool": "risk_check",  "args": {"scope": "timeline"}, "latency_ms": 31, "result_digest": "2 findings, gap=14mo"},
    {"step": 3, "tool": "kb_search",   "args": {"query": "空窗期怎么解释", "top_k": 3}, "latency_ms": 380, "result_digest": "3 hits"},
    {"step": 4, "tool": "impact_check","args": {"section": "work"}, "latency_ms": 27, "result_digest": "4 bullets, 1 quantified"},
    {"step": 6, "tool": "finish",      "args": {"findings": 6, "action_items": 4}, "latency_ms": 0}
  ]
}
```

**为什么这个 timeline 是「agentic 的证明」而不是营销词**：`tool_calls` 的顺序与参数在代码里不存在，是运行时产生的；同一份简历跑两次可能得到不同的工具序列——**"不可预测但可审计"正是 Agent 的特征**，而规则模式的 8 步顺序是常量。

### 4.2 落库与对账

- `agentic` 模式下 `workflow_steps` 仍写 **8 行契约步**（保持 `docs/api-contract.md:240` 不变）：把整段 Agent 运行映射为 `parse` 步（含 trace）+ 结束步；详细 timeline 放在该步的 `output_json`。**契约不破**，前端进度条继续可用。
- 备选（更清晰）：把 trace 放 `analysis_reports.extra_json.agent_trace`。**建议两者都写**：前者给进度/对账，后者给报告页展示。此点需你确认（见 §8 待决 2）。

### 4.3 与规则模式的一致性

| 维度 | 规则模式 | 智能模式 |
|---|---|---|
| 分数 | `compute_scores_deterministic` | **同一个函数** → 同一简历同分 |
| findings schema | `finding_utils` 契约 | **同一契约** |
| 引文校验 | `locate_quote` | **同一函数** |
| 公平性 | `qc_agent` 正则 + 规则四查 | **规则四查必跑**（见下） |
| 步数契约 | 8 步 | 8 步（映射后） |
| 成本/延迟 | 8 次 LLM，约 107–130s | 预算 ≤20 次工具、约 5–8 次 LLM，≤90s |

**公平性与合规是硬约束**：智能模式在 `finish` 之后**必须**再跑一遍规则质检（`qc_agent._check` 的规则四查，不含其 LLM 复核），因为合规红线不能交给模型自觉。这是「智能判断」唯一被允许的规则插手点，且它只做**否决**不做**新增**。

---

## 5. 文件级改动清单

### 5.1 新增（7 个）

| 文件 | 作用 | 预估行数 |
|---|---|---|
| `backend/app/agent/__init__.py` | 导出入口 | ~30 |
| `backend/app/agent/tools.py` | 8 个 tool 的 schema + 执行器 | ~320 |
| `backend/app/agent/orchestrator.py` | Agent 循环、预算、收敛、降级 | ~420 |
| `backend/app/agent/trace.py` | trace 记录与摘要 | ~120 |
| `backend/app/agent/prompts.py` | Agent system prompt（铁律、停止条件、引用要求） | ~120 |
| `backend/scripts/eval_agentic.py` | 评估脚本（30 条 query + 6 份标注简历） | ~260 |
| `frontend/components/agent-trace-panel.tsx` | trace 可视化面板 | ~180 |

### 5.2 修改（9 处，全部为增量）

| 文件 | 改动 | 风险 |
|---|---|---|
| `backend/app/config.py` | 新增 `workflow_mode`、`agent_max_steps`、`agent_max_tool_calls`、`agent_max_retrieval_rounds`、`agent_wall_clock_s`、`agent_max_cost_cny` | 低（纯新增字段） |
| `backend/app/services/llm.py` | **新增** `complete_with_tools(messages, tools, ...)`；现有 `complete_json` 与方法体**不动** | 中（需复用降级链/计价/截断检测，要测） |
| `backend/app/services/rag.py` | `search()` 新增 `role_family` 参数（默认 None = 行为不变） | 低 |
| `backend/app/schemas.py` | `TaskCreate` 新增 `mode` 字段 | 低 |
| `backend/app/api/tasks.py` | 透传 `mode`，落 `analysis_tasks.extra_json.mode` | 低 |
| `backend/app/workflow/graph.py` | 新增 `judge` 节点分支（`mode=agentic` 时把 `parse→…→suggestion` 替换为单节点调用 Orchestrator）；**旧分支一行不动** | 中高（图结构改动，需开关隔离） |
| `backend/app/worker.py` | 透传 mode | 低 |
| `backend/app/models.py` | 复用 `extra_json`，**不加列**（若需索引再议） | 极低 |
| `frontend/`（入口页 + 结果页） | 模式开关 + trace 面板挂载 | 低 |

### 5.3 明确不动的东西

- `backend/app/agents/`（8 个文件）——**一行不改**，规则模式与兜底都依赖它；
- `backend/app/services/analyze.py`、`evidence.py`、`prompts.py`、`finding_utils.py`、`report_render.py`；
- `backend/app/workflow/engine.py`（legacy 手写状态机）；
- `docs/api-contract.md` 的 8 步契约与报告字段。

---

## 6. 分阶段实施与验收

| 阶段 | 内容 | 验收（可执行） | 预估 |
|---|---|---|---|
| **P0** | `eval_agentic.py`：30 条标注 query + 6 份标注简历；输出 recall@3 / MRR / 引用准确率 / 工具调用统计 | 能打印两种模式的对比表；基线数字被固化进仓库 | 0.5 天 |
| **P1** | `tools.py` + `rag.search(role_family)` + `llm.complete_with_tools` | 单测：每个 tool 直接调用返回结构合规；`complete_with_tools` 能拿到 `tool_calls` | 0.5 天 |
| **P2** | `orchestrator.py` + `trace.py` + `prompts.py`：循环、预算、降级、引用校验 | 跑通一份简历，trace 里 `tool_calls ≥ 3` 且**两次运行顺序不同** | 2 天 |
| **P3** | 接线：`config` / `schemas` / `api` / `graph` / `worker` + 前端开关与 trace 面板 | `mode=agentic` 走新链路；`mode=fixed` 时 `verify.py` 全绿（零回归） | 1 天 |
| **P4** | 评估与调优：跑 P0 的评估集，对比两模式；调 prompt 与预算 | **见 §7 指标**；产出对照实验表格 | 1 天 |

**总预估 5 天**（可先交付 P0–P2 的可跑版本，约 3 天）。

---

## 7. 验收指标（智能判断不得声称「检索更准」）

| 指标 | 规则模式（基线） | 智能模式目标 |
|---|---|---|
| 工具调用由模型决定 | ✗（8 步硬编码常量） | ✅ trace 中顺序/参数非常量，两次运行可不同 |
| 检索轮次 | 固定 1 次，`top_k=5` 全链复用 | **≥1 轮由模型触发**，上限 3；`retrieval_rounds` 可统计 |
| query 来源 | `rag.build_query` 模板拼接 | **模型自拟**，且允许针对不同子问题多个 query |
| 引用粒度 | 报告级 top-3 元数据（无正文） | **finding 级 chunk 引用**，`citations_verified` 用 `locate_quote` 复核 |
| 覆盖自适应 | ✗ | 有「首轮不足 → 改写 query 补检索」的实际发生记录（评估集里 ≥5 例） |
| 同一输入分数 | 确定 | **必须相同**（分数仍由规则算，不同即为 bug） |
| 同一输入工具序列 | 常量 | 允许不同（这是特性，不是缺陷） |
| 单份成本 | 实测约 0.1 元级 | ≤0.6 元预算硬上限 |
| 单份延迟 | 107–130s | ≤90s 预算；典型 40–60s（工具多为本地规则） |
| 零回归 | — | `mode=fixed` 下 `scripts/verify.py` 全绿 |
| 合规 | qc 规则四查 | **必跑同一套规则四查**（只否决不新增） |

---

## 8. 需要你拍板的 4 件事

1. **开关默认值**：默认 `fixed`（保守，行为零变化）还是默认 `agentic`（演示效果好但引入成本与不确定性）？**建议默认 `fixed`**，前端开关显式选。
2. **trace 落点**：只落 `workflow_steps.output_json`（不动契约、够用），还是同时落 `analysis_reports.extra_json.agent_trace`（报告页可视化更直接）？**建议两者都写**。
3. **智能模式是否允许跳过某些规则检查**：例如模型判断「这份简历没有 JD，ATS 匹配无意义」时不调 `jd_match_check`。**建议允许**（这正是自主性的体现），但 `qc` 规则四查与分数计算**不可跳过**。
4. **是否保留 HR 批量走规则模式**：`hr_screening` 目前走轻量图（`parse + jd_match + risk`）。若智能模式每份 40–60s × N 份，批量成本会显著上升。**建议 HR 批量默认仍走 `fixed`**，仅单份诊断开放智能模式。

---

## 9. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 模型不按预期调用工具（只输出文本） | 循环空转 | 强约束 prompt + 一次强制 `tool_choice` 指定 `finish`；JSON 协议回退 |
| 工具序列不稳定 → 结果不可复现 | 用户投诉「两次诊断不一样」 | 分数由规则算（已保证）；findings 走同一 `coerce_findings` 与排序；报告中标注「本次分析路径」 |
| 多轮检索推高成本 | 超 0.5 元验收线 | `max_retrieval_rounds=3` + `est_cost_cny` 硬预算 |
| `graph.py` 分支改动破坏现有图不变量 | 现有 174 断言失败 | 新分支用 `mode` 隔离；`build_graph` 的断言保持；先跑 `verify.py` 再合 |
| 前端契约变化 | 前端需同步改 | findings/报告 schema 零变化；仅新增可选 `mode` 与 `agent_trace` 字段，前端按缺省容忍 |
| 供应商 function calling 行为差异（不同模型/网关） | 某层模型不支持 tools | 层内探测 + JSON 协议回退；把「支持 tools」写进供应商能力表 |
