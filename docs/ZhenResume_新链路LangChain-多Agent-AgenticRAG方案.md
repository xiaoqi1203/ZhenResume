# 新链路技术方案：LangChain + LangGraph + 多 Agent + Agentic RAG

> 状态：**待实施**。本文只描述新链路的设计，**不改动 `backend/app/agents/` 下的 8 个旧 Agent**（保留为 `fixed` 对照臂）。
> 决策依据：用户确认"新的用 LangChain，围绕 LangChain / LangGraph / Agentic RAG / 多 Agent 这些主流技术推进"。
> 配套文档：[语义判断接入与Agent封装_改动说明书](ZhenResume_语义判断接入与Agent封装_改动说明书.md)（规则层改动与验收）、
> [改造方案总览](ZhenResume_智能判断Agentic改造方案.md)

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

```
                    ┌──────────────────────────────────────────┐
                    │  LangGraph 主图（新链路，WORKFLOW_MODE=agentic）│
                    └──────────────────────────────────────────┘
START → setup → retrieve(首轮 RAG，可被 agent 覆盖)
      → supervisor ──┬─→ ats_agent      （LangChain create_agent + tools）
                     ├─→ impact_agent
                     └─→ risk_agent
                     ↓ （超步汇聚，Send 扇出）
              evidence_validator  ← ★ 证据硬校验（复用 services/evidence.locate_quote）
                     ↓
              qc_gate             ← ★ 规则四查（复用 qc_agent._check，只否决不新增）
                     ↓
              finalize(compute_scores_deterministic + score_v2) → persist → END
```

**三条不可动摇的边界**（沿用既有架构纪律）：

1. **分数只由规则算**：`analyze.compute_scores_deterministic`，LLM 的判断必须变成"可校验的证据"才能进分母（详见改动说明书 §4.1/§10.3）；
2. **合规只由规则判**：`qc_gate` 用现有正则四查，Agent 不得绕过；
3. **旧链路一行不改**：`WORKFLOW_MODE=fixed` 时走原 `build_graph`，`scripts/verify.py` 174 项必须全绿。

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

### 5.2 检索器封装

```python
class KnowledgeRetriever:
    """把 services/rag.search 包成 LangChain 可用形态（不引入 VectorStore 抽象）。"""
    def search(self, query: str, *, top_k=5, scope=None, role_family=None) -> list[dict]
```

- 复用现有混合打分与**模型一致性守卫**（查询向量与库内向量不同模型时退化为纯词面，`rag.py:98-122`）；
- `role_family` 参数是新增接线（改动说明书 §4.5）。

---

## 6. 目录结构

```
backend/app/agentic/                  ← 全新包，与旧 agents/ 并存
├── __init__.py
├── state.py            # AgenticState（含 reducer）、预算配置
├── llm_bridge.py       # ★ §3.1：ChatOpenAI 构建 + 降级链 + 错误语义
├── callbacks.py        # ★ §3.2：WorkflowStepCallback（落 workflow_steps）
├── tools.py            # @tool 定义（包装 services/analyze、rag、evidence）
├── retriever.py        # §5.2 KnowledgeRetriever
├── agents.py           # ats / impact / risk 的 create_agent 定义
├── supervisor.py       # 路由 + Send 扇出 + 预算
├── validators.py       # ★ §3.3 证据硬校验 + qc 规则四查接入
└── graph.py            # 主图装配（复用 workflow/persist、finalize 节点）
```

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

---

## 10. 待确认

| # | 事项 | 建议 |
|---|---|---|
| 1 | 依赖版本组合（`langchain` / `langchain-openai` 具体 pin） | 先做 L0 探针后锁定，写入 requirements |
| 2 | 是否把 `supervisor` 做成 LLM 路由（更"多 Agent"）还是确定性扇出（更稳） | **确定性扇出优先**（三个维度必跑），把 LLM 用在子 Agent 内部 |
| 3 | HITL 是否本期做（L5） | 建议放到 L4 之后，先拿到对照实验数字 |
| 4 | 是否为 agentic 链路单独起一张 trace 表（便于查询） | 建议先用现有 `workflow_steps.output_json`，不建新表 |
