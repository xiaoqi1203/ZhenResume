# 新链路技术方案：LangChain + LangGraph + 多 Agent + Agentic RAG

> 状态：**已实施**（2026-10-18）。`WORKFLOW_MODE=fixed` 走原链路（回归 174/174），
> `WORKFLOW_MODE=agentic` 走 agent 线（finalize 之前的分析由 Agent 自主调工具完成）。
> 旧 `backend/app/agents/` 下的 8 个 Agent **一行未改**，作为 fixed 对照臂保留。
> 决策依据：用户确认"新的用 LangChain，围绕 LangChain / LangGraph / Agentic RAG / 多 Agent 这些主流技术推进"。
> 配套文档：[语义判断接入与Agent封装_改动说明书](ZhenResume_语义判断接入与Agent封装_改动说明书.md)（规则层改动与验收）、
> [改造方案总览](ZhenResume_智能判断Agentic改造方案.md)

---

## 0.1 先厘清术语：两条线到底差在哪（**最容易搞混的一节**）

项目里同时存在「RAG」「固定检索」「Agentic RAG」「agent 线」这些说法，历史文档里曾混用。
按实现事实，**唯一准确的划分是这样**：

| | **fixed 线** | **agent 线** |
|---|---|---|
| 对外叫法 | 固定流水线 + **RAG（固定检索）** | Agent 自主循环 + **Agentic RAG** |
| 谁发起检索 | **代码**：`retrieve_node` 进图跑一次 | **模型**：自己决定何时调 `kb_search` |
| query 谁写 | **代码**：`rag.build_query()` 拼字符串 | **模型**：每轮针对子问题自拟 |
| 检索几次 | 固定 1 次（`top_k=5`） | 模型决定（实测量子 3 轮） |
| 能否换 query 重查 | ❌ 不能 | ✅ 能，可据上一轮结果调整 |
| 能否下钻 | ❌ 只有摘要（`knowledge_digest` 截 300 字） | ✅ 可 `read_chunk` 读切片全文 |
| 引用 | 报告级 top-3 元数据 | **finding 级** `{chunk_id, quote}`，逐字校验 |
| 分析阶段怎么做 | LLM **单次调用**（`complete_json`），上下文一次性塞满 | `create_agent` + 工具，**有界循环**（默认 ≤10 次往返） |

### 三个必须说清的纠正

**① fixed 线**不是**「没有检索」，也不是「LLM 检索」**
它的检索是**纯代码拼 query + 向量检索，全程无 LLM 参与**：

```python
# services/rag.py:build_query —— 纯字符串拼接
query = jd.title + jd.seniority + jd.skills_core[:6] + jd.keywords + resume.skills[:8]
rag.search(db, query, top_k=5)
```

准确说法是 **fixed RAG / 一次性检索**。把 fixed 说成"LLM 检索"是错的 —— fixed 线里有 LLM，
但**检索的决策权不在模型手里**。

**② Agentic RAG 的定义就是"检索策略由模型自主决定"**
判定一条链路是不是 Agentic RAG，只看一件事：**模型能不能自己决定检索的时机、query、轮次与下钻**。
按这个判据：fixed 线是 RAG，**agent 线才是 Agentic RAG**。
（fixed 线**有 RAG**，但不是 agentic —— 别把两者对立成"有/无 RAG"。）

**③ 两条线的分数必然相同，这是设计而非巧合**
评分入口 `finalize_node` **不读任何 findings**：

```python
# workflow/nodes.py
def finalize_node(state):
    return {"scores": analyze.compute_scores_deterministic(
        ctx.parsed_resume, ctx.parsed_jd, ctx.role_family,
        ctx.resume_text, ctx.jd_text,     # ← 只有原始输入，没有 ctx.findings
    )}
```

`compute_scores_deterministic` 内部会**现场重算一遍规则基线**（`rule_baseline_findings`），
所以两条线展示出来的 findings **都不参与评分**。

> 为什么必须这样：若评分跟着 Agent 走，同一份简历两次诊断分数会不同（实测风险分 78↔95），
> 「改完简历重新测、对比分数变化」这个卖点就失效，HR 排名也无法解释。
> 代价是：**agent 线的价值全在"结论质量与依据"，不在"分数更准"**。详见 §8.3。

---

## 0. 依赖现状（已实测）

```
langchain            -          ← 未安装（本次要加）
langchain-core       1.6.10     ← 已有（langgraph 传递依赖）
langgraph            1.2.15     ← 已有，requirements 显式声明
langchain-openai     -          ← 未安装（本次要加）
langsmith            0.14.8     ← 传递依赖（不开 tracing）
httpx                0.28.1     ← 现有唯一 HTTP 客户端
```

网络可用（已实测 `pip download langchain-openai` 成功）。`langchain-core 1.6.10` 说明 LangChain 生态已到 **1.x**，API 与 0.x 有代差，本方案按 1.x 写。

**新增依赖（待确认版本）**

| 包 | 作用 | 备注 |
|---|---|---|
| `langchain` | 1.x 稳定层：`create_agent` / `@tool` / 中间件 | 需要确认与 `langchain-core 1.6.10` 的兼容版本 |
| `langchain-openai` | `ChatOpenAI`（走 OpenAI 兼容协议，适配百炼/DeepSeek） | 替代手写 `httpx` 调用 |
| （沿用）`langgraph` 1.2.15 | 图编排 / checkpoint / `Send` / `interrupt` | 已装 |

> ⚠️ 落地时先锁版本（例如 `langchain==1.2.x` + `langchain-openai==1.7.x`），并在 `requirements.txt` 写明"与 langchain-core 1.6.10 对齐"。

---

## 1. 架构总览

### 1.1 实际落地的 agent 线（2026-10-18 已实施）

> ⚠️ 与本文 §1.2 的**原设计有差异**，以下为**实际实现**。差异原因写在 §1.3。

```
START → setup → retrieve（fixed RAG：模型不参与，代码拼 query 检索一次）
      → parse（规则 + 单次 LLM 纠正；结构化，无需多轮）
      → jd_match ─┐                    ← 以下四个节点在 agentic 模式下
      → ats       ├─ Agent 自主循环    全部换成 create_agent + tools
      → impact    │   （并行扇出）     四个节点共用同一套 8 个工具
      → risk     ─┘
      → merge → suggestion（确定性收敛，不 Agent 化）
      → report → qc（规则四查 + LLM 复核）
      → agentic_evidence（可选，非契约节点：为最终 findings 挂 chunk 级引用）
      → finalize（★ 规则评分：只吃原始输入，不读 findings）
      → persist → END
```

**代码位置**

| 模块 | 作用 |
|---|---|
| `app/agentic/analyzers.py` | 四个分析 Agent 的 `AgentSpec`（提示词、工具集、预算、`finish_model`） |
| `app/agentic/runtime.py` | 通用 Agent 循环：模型装配、工具装配、预算、trace、强制收尾 |
| `app/workflow/agent_nodes.py` | 把 Agent 产出接回图状态：服务端复核 + 与规则基线合并 |
| `app/workflow/graph.py` | `build_graph(workflow_mode=...)` 按模式切换分析节点 |
| `app/services/agent_tools.py` | 9 个只读工具（含 2 个批量工具，见 §5.3） |

### 1.2 原设计（未采用，保留作为对照）

```
START → setup → retrieve(首轮 RAG，可被 agent 覆盖)
      → supervisor ──┬─→ ats_agent      （LangChain create_agent + tools）
                     ├─→ impact_agent
                     └─→ risk_agent
                     ↓ （超步汇聚，Send 扇出）
              evidence_validator  ← ★ 证据硬校验
                     ↓
              qc_gate             ← ★ 规则四查
                     ↓
              finalize → persist → END
```

### 1.3 与本设计的四处差异（及原因）

| 设计 | 实际 | 原因 |
|---|---|---|
| `supervisor` 节点做路由 | **没有 supervisor**，用 LangGraph 静态边扇出 | 设计文档 §7 自己已定"确定性扇出优先（三个维度必跑），把 LLM 用在子 Agent 内部"。LLM 路由在这里只会增加一次往返与不确定性 |
| `evidence_validator` 独立节点 | **没有独立节点**，校验内嵌在每个分析节点的 `_to_findings()` 里 | 引文核验必须**跟着每一条结论走**（哪个 Agent 给的引文，就在那个节点校验并标记 `unverified`）。做成独立节点要么重复遍历、要么丢失"这条引文是谁给的" |
| 三个子 Agent（ats/impact/risk） | **四个**（多一个 `jd_match`） | `jd_match` 承担"区分真差距与表述差异"，是唯一允许推翻规则层结论的维度，必须给它工具才能真的核实 |
| 只在 `fixed` 保留旧 Agent | **旧 `agents/` 一行未改**，fixed 线完全走原实现 | 保证 `scripts/verify.py` 174 项可回归（已实测 174/174） |

**四条不可动摇的边界**（沿用既有架构纪律，已实测守住的）：

1. **分数只由规则算**：`analyze.compute_scores_deterministic`，**不读 Agent 产出的 findings**（见 §0.1 ③）；
2. **合规只由规则判**：`qc` 用现有正则四查，Agent 不得绕过（agent 线曾被它误杀一条合规建议，已修，见 §9）；
3. **旧链路一行不改**：`WORKFLOW_MODE=fixed` 走原节点，`verify.py` **174/174** 全绿；
4. **8 步契约不破**：agent 线的分析节点名字逐字沿用 `parse/jd_match/ats/impact/risk/...`，
   `workflow_steps` 仍是 8 行，前端时间线与进度条无需改动。

---

## 2. 技术选型：什么用 LangChain，什么不用

### 2.1 用 LangChain 的部分

| 能力 | 用什么 | 替代了什么 |
|---|---|---|
| 模型调用 | `ChatOpenAI`（`base_url` 指向百炼/DeepSeek） | 手写 `httpx.post`（仅新链路） |
| 工具定义 | `@tool` + Pydantic 入参模型 | 手写 JSON Schema |
| Agent 循环 | `create_agent(model, tools, ...)` | 手写 for 循环（仅新链路） |
| 结构化输出 | `with_structured_output(Pydantic)` | `_extract_json` 手写解析（仅新链路） |
| 消息与状态 | `messages` / `AgentState`（`add_messages`） | 自己维护 list[dict] |
| 多 Agent 编排 | LangGraph `StateGraph` + `Send` | 现有 `asyncio.gather`（HR 批量） |
| 人机回环 | LangGraph `interrupt` / `Command` | 无（现在完全没有 HITL） |
| RAG 检索器接口 | 自定义 Retriever 包装 `services/rag.search` | 无（直接函数调用） |

### 2.2 不用的部分（以及为什么）

| 不用 | 原因 |
|---|---|
| `langchain_community` 全家桶 | 只需 chat + tools，装上会带大量无关键与重依赖 |
| LangChain 的 memory / VectorStore 抽象 | 向量库是 SQLAlchemy 表（`knowledge_chunks`），已有检索实现；换抽象等于重写 |
| `langchain.retrievers` 内置检索链 | 现有 `rag.search` 的**混合打分（0.6 向量 + 0.4 词面）+ 模型一致性守卫**比默认实现更贴合本项目 |
| LangSmith tracing | `workflow_steps` 是既有契约表；开 LangSmith 会外发简历文本（隐私） |
| Agent Server / langgraph-cli | requirement 注释已明确排除（Elastic-2.0 license + 外联 beacon） |

---

## 3. 必须自己补齐的三件事（LangChain 不管）

这三条是**新链路能否守住现有质量的关键**，LangChain 不提供等价物。

### 3.1 降级链与错误语义（现有 `services/llm.py` 的核心资产）

现有实现里有三条用真实事故换来的规则，**必须在新链路上重现**：

| 现有机制 | 代码位置 | LangChain 侧的对应做法 |
|---|---|---|
| `LlmTruncated`：`finish_reason=length` 时**跳过重试直接换模型** | `llm.py:38-45, 300-310` | 关闭 SDK 自动重试（`max_retries=0`），捕获长度类异常后由自定义 `with_fallbacks` 切模型 |
| `_ensure_json_hint`：提示词缺 "json" 时百炼/DeepSeek 直接 400 | `llm.py:63-75, 170-179` | 在调用前统一注入；用 `with_structured_output` 时同样要保证 prompt 含 json |
| extra_body 被拒（400）→ 去掉额外字段重试一次 | `llm.py:276-288` | 自建 `Runnable` 包装或自定义异常处理，**不能用 SDK 默认重试** |
| 供应商 × 模型双层阶梯 | `config.llm_chain` | `primary.with_fallbacks([m2, m3, ...])` 串起来 |
| 成本/token 落 `workflow_steps` | `AgentOutput.tokens/cost/latency_ms` | **自定义 Callback Handler**（见 §3.2） |

> 实现要点：把这段逻辑做成一个适配器 `app/agentic/llm_bridge.py`，对外暴露 `build_chat_model(spec)` 与 `invoke_with_fallback()`，**所有 LangChain 调用统一走它**，避免"两套错误语义"。

### 3.2 可观测性（复用 `workflow_steps`，不开 LangSmith）

```python
class WorkflowStepCallback(BaseCallbackHandler):
    """把 LangChain 的 LLM/Tool 事件收集成一行 workflow_steps 记录。"""
    def on_llm_end(self, response, **kw):        # token usage / model 名 / 耗时
    def on_tool_start(self, serialized, input_str, **kw):
    def on_tool_end(self, output, **kw):
    def on_chain_error(self, error, **kw):        # 降级原因必须落 notes（不静默）
```

产出结构与现有 trace 对齐：`steps / tool_calls / retrieval_rounds / citations_verified / cost / latency_ms`。

### 3.3 证据硬校验（LangChain 无此概念）

`evidence_validator` 节点**不调用任何 LLM**，只做：

```python
for item in collected_judgments:
    if locate_quote(resume_text, item.quote) is None:   # services/evidence.py
        drop(item)                                      # 作废，并计入 citations_failed
```

任何 Agent 输出的结论，**没有可定位引文就不进分数**。这是全套设计可信度的唯一来源。

---

## 4. 多 Agent 设计

### 4.1 Agent 清单与职责

| Agent | 工具集 | 输出 | 记忆/状态 |
|---|---|---|---|
| `supervisor`（路由） | 无（纯决策） | `next: ats\|impact\|risk\|validator\|FINISH` | 读全局 state |
| `ats_agent` | `jd_requirements` / `ats_check` / `kb_search` / `read_chunk` / `resume_search` | `Finding[]` + `evidence[]` | 独立 messages |
| `impact_agent` | `impact_check` / `read_section` / `kb_search` / `resume_search` | 同上 | 独立 messages |
| `risk_agent` | `risk_check` / `timeline_check` / `read_section` / `locate_quote` | 同上（合规项） | 独立 messages |
| `evidence_validator` | 无（纯函数） | 校验后的 `verified[]` / `failed[]` | — |
| `qc_gate` | 无（规则四查） | `qc_report` | — |

**为什么是 3 个子 Agent 而不是 1 个**：三个维度的**工具集与判断标准不同**（ATS 看关键词与格式、impact 看量化与上下文、risk 看时间线与合规），且可并行。这正是"多 Agent"在本项目里成立的理由——不是为数量而数量。

### 4.2 扇出与汇聚（LangGraph `Send`）

```python
def route_from_supervisor(state):
    return [Send("ats_agent", {...}), Send("impact_agent", {...}), Send("risk_agent", {...})]

# state 中 findings 用 reducer 汇聚（与现有 finding_batches 同思路）
class AgenticState(TypedDict):
    findings: Annotated[list, operator.add]
    agent_trace: Annotated[list, operator.add]
```

⚠️ `Send` 的 payload 必须自带该子任务的**全部输入**（子图看不见兄弟节点），已在既有文档中记录过这个约束（`ZhenResume_LangGraph接入与并发实现.md:937`）。

### 4.3 预算与收敛（防止跑飞）

| 预算 | 默认 | 超限行为 |
|---|---|---|
| 每个子 Agent 的 `recursion_limit` | 8 | 强制输出当前结论 |
| 全局 `max_tool_calls` | 24 | supervisor 收到 `FINISH` 指令 |
| `max_retrieval_rounds` | 3 | `kb_search` 返回"已达上限" |
| 墙钟 | 90s | 走已收集的结论 |
| 成本 | ≤¥0.6/份 | 同上 |

### 4.4 人机回环（HITL，LangGraph `interrupt`）——HR 场景

HR 批量初筛时，对**灰区候选人**（规则分处于批次中位区间且 `evidence_failed > 0`）暂停：

```python
decision = interrupt({"reason": "证据不足，需人工确认", "candidate": ..., "evidence": ...})
# 外部 POST /hr/batches/{id}/resume  →  Command(resume={"verdict": "keep"|"reject"})
```

这是现有项目**完全没有**的能力，也是引入 LangGraph `interrupt` 的唯一充分理由。

---

## 5. Agentic RAG 设计

### 5.1 与现状的差异

| | 现状（fixed） | 新链路（agentic） |
|---|---|---|
| 检索时机 | 进图前一次 | Agent 自主决定（可不检索 / 多次） |
| query | `rag.build_query` 模板拼接 | 模型针对子问题自拟（多 query） |
| top_k | 固定 5（`rag_top_k` 未被读取） | 工具参数，模型可调 |
| 注入量 | top-3 × 300 字（`knowledge_digest`） | 按需 `read_chunk` 取全文 |
| role_family 过滤 | **死代码**（有列有值，从没接线） | `kb_search(role_family=...)` 真正生效 |
| 引用 | 报告级 top-3 元数据 | **finding 级 chunk 引用 + `locate_quote` 复核** |

实测（同一份简历，`f9bef032` vs `997ed982`）：

| | fixed | agentic |
|---|---|---|
| 结论条数 | 8 | 8 |
| 有原文定位的结论 | 7 | 7 |
| **有知识库依据的结论** | **0** | **7（共 9 个 chunk 引用）** |

### 5.2 检索器封装

不引入 `VectorStore` 抽象 —— 直接复用 `services/rag.search`，在工具层包一层：

```python
# services/agent_tools.py:ToolRunner._kb_search_async
async def _kb_search_async(self, a: KbSearchArgs) -> ToolResult:
    hits = await asyncio.to_thread(
        lambda: rag.search(db, a.query, top_k=a.top_k, scope=a.scope,
                           role_family=a.role_family or self.role_family)
    )
```

- 复用现有混合打分与**模型一致性守卫**（查询向量与库内向量不同模型时退化为纯词面，`rag.py:98-122`）；
- `role_family` 参数已接线；
- **同步 IO 放线程池**，避免阻塞事件循环（批量运行时尤其重要）。

### 5.3 工具集（9 个，全部只读）

| 工具 | 用途 | 谁用 |
|---|---|---|
| `jd_requirements` | 看岗位要求清单（核心/加分/关键词/职责） | 全部分析 Agent |
| `resume_search` | 单关键词查找（需要看上下文片段时） | 全部分析 Agent |
| **`resume_batch_search`** | **批量核验多个关键词是否出现过** | ats / jd_match / risk |
| `read_section` | 读简历某区块全文（work/projects/skills/education） | 全部分析 Agent |
| `locate_quote` | 单条引文自检 | 全部分析 Agent |
| **`locate_batch`** | **批量核验多条引文能否定位** | 全部分析 Agent |
| `kb_search` | 检索知识库（能力模型/表达规范/合规） | 全部分析 Agent |
| `read_chunk` | 下钻读知识库切片全文 | 全部分析 Agent |
| `rubric_get` | 取本次判定准则 | evidence |

#### 为什么必须有两个「批量」工具（实测教训）

第一版只给了单数版本，结果（2026-10-18 实测）：

```
ats Agent: 12 次模型往返 / 28 次工具 / 55 秒 / 最后预算耗尽被截断 → 产出为空
工具序列: jd_requirements → read_section
         → resume_search ×10     ← 为 10 个缺失关键词各查一次
         → locate_quote ×6       ← 为 6 条引文各验一次
         → kb_search → read_section
```

**这就是"Agent 自主"的代价被工具粒度放大的典型**：模型会诚实地逐个核验，
但每次核验都是一次完整往返。加批量工具后：

```
同一个 Agent: 8 次往返 / 11 次工具 / 47 秒 → status=done，产出 5 条结论
```

**结论：给 Agent 的工具，粒度要匹配它要做的判断批量**，否则预算会被"正确但琐碎"的调用吃光。

### 5.4 预算（实测调参，别按"最少步数"给）

```python
_ANALYSIS_BUDGET = AgentBudget(
    max_model_calls=10,     # 第一版给 6 → 四个 Agent 全部被截断、产出为空
    max_tool_calls=40,
    max_retrieval_rounds=2,
    wall_clock_s=150,
)
```

**实测的自然收敛轨迹**（risk Agent）：`read_section ×2 → resume_batch_search → kb_search → locate_batch → kb_search → finish`
= **4 次往返 / 7 次工具**。给 6 次往返看似够，但模型偶尔会多核实一轮就超了 —— 而超预算的后果是
**整轮产出为 0**（不是"少几条"）。所以预算要给到自然值的 2-2.5 倍。

### 5.5 两个必须的兜底（踩过才知道）

1. **`forced_finish` 要沿降级链试**：预算耗尽时强制模型只调 `finish`，但**只试主模型会失败**
   （主模型 403 时兜底也 403），结果是"花了 15 秒、产出为空"。改成按 `primary → fallbacks` 逐个试。
2. **收到 `finish` 立即中断循环**：`create_agent` 在工具返回后还会再跑一次往返去"总结"
   （实测输出 8 token、耗 1.8s，对结果零贡献）。主动 break 掉。

### 5.6 逐次往返耗时要落 trace（排查慢的唯一依据）

`trace.call_timings` 记录每次模型往返的 `elapsed_ms / tokens_in / tokens_out / tools / context_messages`。
没有它只能看到"总共 34s"，看不出是首轮冷启动、某次输出爆炸、还是上下文重发导致的累积变慢。

实测结论（risk Agent 34s 的构成）：

```
第1次   4536ms  in= 3515 out=  90
第2次   2502ms  in= 3795 out= 123
第3次   3368ms  in= 5896 out= 177
第4次   2187ms  in= 6423 out= 140
第5次   4437ms  in= 6866 out=  82
第6次  15252ms  in= 7100 out=1090   ← finish：一次吐全部结论，占 45%
第7次   1766ms  in= 8212 out=   8   ← 空转（已消除）
```

**瓶颈不是"多轮"，而是"最后一次要一口气输出全部结论"**。修法：
给 `AgentFinding` 每个字段加 `max_length`（`title` 40 / `issue` 120 / `rewritten_text` 260），
把那次输出压下来 —— 单 Agent **34.1s → 19.3s（-43%）**。

---

## 6. 目录结构（实际落地）

```
backend/app/agentic/                  ← 与旧 agents/ 并存的 Agent 框架
├── __init__.py
├── runtime.py          # AgentSpec / AgentBudget / BaseAgent（有界循环 + trace + 墙钟超时 + 强制收尾）
├── models.py           # settings.llm_chain → ChatOpenAI 列表（复用降级链语义）
├── registry.py         # Agent 注册表（get_spec / register）
├── analyzers.py        # ★ 四个分析 Agent 的 AgentSpec（jd_match / ats / impact / risk）
├── evidence.py         # knowledge_evidence Agent（qc 之后挂 chunk 级引用）
└── (无 supervisor.py / validators.py —— 见 §1.3)

backend/app/workflow/
├── agent_nodes.py      # ★ Agent 产出 → 服务端复核 → 与规则基线合并 → 写回图状态
├── graph.py            # build_graph(workflow_mode=...) 按模式切换
├── steps.py            # record_step（*_digest → *_json 字段名映射，修了静默丢数据）
└── (原 nodes.py / state.py / persist.py 未改)

backend/app/services/
└── agent_tools.py      # 9 个只读工具（含 resume_batch_search / locate_batch）
```

**与旧代码的关系**：`agentic/` 单向依赖 `services/*` 与 `workflow/*`；
**旧 `agents/` 不被 import、也不被修改**。

**与旧代码的关系**：`agentic/` 单向依赖 `services/*` 与 `workflow/persist.py`；**旧 `agents/` 不被 import、也不 import 新包**。

---

## 7. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| **L0** | 锁依赖版本 + `llm_bridge`（含三条错误语义）+ `callbacks` | 用新桥打印出与旧链一致的 token/cost；截断时**跳过重试换模型**；extra_body 400 能降级 |
| **L1** | `tools.py`（8 个 `@tool`）+ `retriever.py` | 每个 tool 可单独调用并返回结构合规结果；`kb_search(role_family=)` 生效 |
| **L2** | 单 Agent 版（只有 `ats_agent`）+ 主图骨架 + `evidence_validator` | 真实简历跑通；trace 落 `workflow_steps`；无引文的结论被作废 |
| **L3** | 多 Agent（supervisor + 3 子 Agent + `Send` 扇出） | trace 里出现并行工具调用；三个 Agent 的 findings 正确汇聚 |
| **L4** | `qc_gate` + `finalize`（`score_v2`）+ `WORKFLOW_MODE=agentic` 接线 | `mode=fixed` 下 `verify.py` 全绿；`mode=agentic` 出报告 |
| **L5** | HITL（`interrupt`）用于 HR 灰区候选人 | 批次跑到灰区暂停，外部 `Command(resume=...)` 续跑 |
| **L6** | 对照实验（`eval_agentic.py --engine agentic`） | 产出 fixed vs agentic 的 recall/precision/成本/延迟对比表 |

---

## 8. 验收指标

| 指标 | fixed（单次调用） | agentic（新链路） |
|---|---|---|
| 假差距 recall | 由 P-1 基线给出 | **≥ 基线**（否则 agent 循环无价值） |
| 假差距 precision | 同上 | ≥ 基线 |
| 证据定位通过率 | 同上 | ≥ 基线（多了 agent 内自检） |
| 工具调用 | 0（无工具） | ≥3/份，顺序不固定 |
| 检索轮次 | 固定 1 | 可由模型触发多轮 |
| 同输入重跑一致性 | 100% | **仍 100%**（分数由规则算 + 缓存） |
| `mode=fixed` 回归 | — | `verify.py` 174 项全绿 |
| 隐私 | 不外发 | **不开 LangSmith**，trace 只落本地表 |

---

## 9. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| LangChain 1.x API 与 0.x 差异 | 实现返工 | L0 第一步先跑通最小 `create_agent` 探针，再写业务 |
| 降级语义在新链路上退化 | 线上稳定性下降（这是最贵的风险） | §3.1 三条规则逐条写成测试；`llm_bridge` 是唯一入口 |
| 两套链路结果不一致 | 用户困惑 | 只在 `mode=agentic` 启用；`fixed` 路径零改动；对照实验量化差异 |
| Agent 循环成本/延迟失控 | 超预算 | 四类预算硬上限（§4.3）+ trace 记录截断原因 |
| 旧依赖升级牵连 `langgraph 1.2.15` | 现有图回归 | 锁版本；新包不升级已有 pin |
| 隐私（简历文本外发） | 合规 | 关闭 LangSmith；只走已配置的 LLM 供应商（与现状同） |
| **Agent 产出的是"真问题"，但分数不动** | 用户感知不到深度价值（**当前最大缺口**，见 §11） | 待决策：拆双分 / Agent 分反哺 |
| **qc 正则误杀合规建议** | agent 线主动发现"简历含性别年龄→建议删除"，被当成歧视内容 drop（实测公平性 0.8571） | 已修：区分"评价候选人"与"提醒删除敏感字段"（`_looks_discriminatory`） |

---

## 10. 待确认

| # | 事项 | 建议 |
|---|---|---|
| 1 | 依赖版本组合（`langchain` / `langchain-openai` 具体 pin） | 已落地；`requirements.txt` 已锁 |
| 2 | 是否把 `supervisor` 做成 LLM 路由 | **已决定：不用 LLM 路由**，确定性扇出（见 §1.3） |
| 3 | HITL 是否本期做（L5） | 建议放到 L4 之后，先拿到对照实验数字 |
| 4 | 是否为 agentic 链路单独起一张 trace 表 | 已用现有 `workflow_steps.output_json` + 报告 `extra_json.agent_trace`，未建新表 |

---

## 11. 实测数据（2026-10-18，真实简历 × 后端 JD）

### 11.1 agent 线跑通状态

| 节点 | 模型往返 | 工具调用 | 耗时 | Agent 结论 | 基线 | 合并 |
|---|---|---|---|---|---|---|
| jd_match | 4 | 7 | 22.2s | 5 | 1 | 6 |
| ats | 5 | 7 | 27.9s | 5 | 1 | 6 |
| impact | 5 | 7 | 25.5s | 5 | 3 | 6 |
| risk | 3 | 6 | 23.3s | 5 | 0 | 5 |

- 8 步契约不变，`progress=1.0`，`status=done`；
- 每步 `output_json.trace` 可查完整工具序列；
- `jd_match` 独立判定 `Spring Boot` 为表述差异（简历写 `SpringBoot`），避免让用户补一个"其实具备"的能力。

### 11.2 两条线对比（同一份简历 `997ed982`(fixed) vs `f9bef032`(agentic)）

| | fixed | agentic |
|---|---|---|
| 五维分数 | 78 / 54 / 100 / 95 / 58 / 95 | **完全一致** |
| 结论条数 | 8 | 8 |
| 有原文定位 | 7 | 7 |
| **有知识库依据** | **0 条** | **7 条（9 个引用）** |
| 端到端 | **~35s** | **106s** |
| 成本 | **¥0.02** | **¥0.40** |

**agentic 独有的结论**（fixed 完全抓不到）：
- `个人优势是占位文本` —— 首屏放占位文字，HR 会判为模板未填（语义判断，正则写不出来）
- `乙公司 5 个月仅一句职责` —— 短期任职无产出说明，HR 会追问原因
- `核心关键词覆盖率不足：JD 14 项仅命中 4 项` —— 给出具体数字，而非只说"覆盖率 25%"

**改写句质量差异**（同一维度）：
- fixed：`原文 + [补充指标：QPS]`（规则拼接）
- agentic：`主导订单接口重构，引入缓存与批量查询，支撑日均 100 万次调用，P99 从[补充]降至[补充]ms`
  （读懂了内容——认出"100 万次调用"是规模、缺的是延迟与可用性）

### 11.3 性能优化过程（都有实测依据）

| 优化 | 前后 |
|---|---|
| `finish` 后主动中断循环（消除空转往返） | 单 Agent -1.8s |
| `AgentFinding` 字段 `max_length` | risk Agent 34.1s → **19.3s（-43%）**，token 43.5k → 26.8k |
| 两个批量工具替代逐个调用 | ats Agent 12 往返/28 工具/55s 且**产出为空** → 8 往返/11 工具/47s **产出 5 条** |
| 预算 6 → 10 次往返 | 四个 Agent 全部 `truncated`（产出空）→ 全部 `done` |
| 端到端（以上合计） | 154.9s → **106.0s（-32%）** |

### 11.4 回归

- **`WORKFLOW_MODE=fixed`：`verify.py` 174/174 全部通过**（含 8 步契约、报告结构、证据链、
  两次诊断分数一致、HR 批量等）；
- 两图编译自检：`fixed` 13 节点 / `agentic` 14 节点，契约 8 步均齐备。

---

## 12. 待决策：agent 线的价值如何让用户感知到

**这是当前最大的产品缺口，不是技术问题。**

现状：agent 线慢 3 倍、贵 20 倍，但**分数与 fixed 完全一样**（§0.1 ③ 的设计后果）。
用户付了 20 倍的钱，看到的分数没变，只多了些"依据"和"更具体的改写" —— 这个价值差很难被感知。

三个候选方向：

| 方案 | 做法 | 风险 |
|---|---|---|
| **A. 维持现状** | 分数恒由规则算，agent 线只负责"解释更清楚、依据更扎实" | 深度的价值难感知；付费理由弱 |
| **B. 双分并列（推荐）** | 报告同时展示 `规则分`（78，可比、可回归）与 `深度分`（如 71，Agent 发现更多问题） | 要解释"两个分为什么不同"，否则用户困惑 |
| **C. Agent 结论反哺评分** | 让 Agent 产出的 findings 以受限权重参与扣分 | **会破坏「同一简历两次同分」的承诺**（该问题以前实测发生过：风险分 78↔95；项目文档把它列为必须修复的 bug） |

**建议 B**：`规则分` 保证可比较、可回归（"我改完简历，分数从 78 涨到 85" 仍然成立）；
`深度分` 体现 Agent 发现的问题（"规则匹配 78 分，但深度分析发现 3 个规则没抓到的问题，实际竞争力约 71 分"）。
两者口径写清楚，用户既能横向比较，也能看到深度分析多抓到了什么。
