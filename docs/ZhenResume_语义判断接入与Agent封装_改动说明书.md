# ZhenResume 语义判断接入与 Agent 封装 —— 改动说明书

> **状态：待评审。本文档不包含任何对生产代码的修改**，仅精确描述"要改哪些文件、改成什么形状"。
> 决策依据：
> - **R1 = LLM 出结构化判断 + 原文证据 → Python 校验 → 与规则共同算分**（用户确认）；
> - **§13-1 = b：新档位 `score_v2`**，保留 `score_v1`（用户确认）→ 见 §12；
> - **分层加权采纳**（用户提出）：LLM 给 JD 条目打**核心/加分标签**，规则层读预配置权重 → 见 §10。
> - R2（语义限幅）与 R3（权重配比）**延期**：先接通、不引入新旋钮，等评估集数据说话（见 §9）。
> 配套文档：[改造方案总览](ZhenResume_智能判断Agentic改造方案.md)、`aaa.md` §3.6/§五.3

---

## 0. 一句话目标

给现在这套"关键词子串匹配 + 规则评分"系统，**接通它本就设计好的语义入口**，并用真正的 Agent 封装（LLM 自主选择 tool、自证证据）来实现它；分数公式、权重、报告契约一律不动。

---

## 1. 原项目的问题：两次断线（已用真实样例实测）

### 1.1 断线一：字段名不匹配，LLM 结论拿不到

| 位置 | 内容 |
|---|---|
| `agents/jd_match_agent.py:70-73` | 读 `data.get("real_gaps_as_findings")` |
| `services/prompts.py:39-40` | 要求模型输出 `real_gaps` / `false_gaps` |

→ **模型永远不会输出 `real_gaps_as_findings`**，该分支恒为空，是死代码。

### 1.2 断线二：就算拿到了，也不影响分数（更根本）

```python
# services/analyze.py:119-133（逐字）
def missing_keywords(parsed_resume, parsed_jd, raw_text="") -> tuple[list[str], list[str]]:
    haystack = resume_haystack(parsed_resume, raw_text).lower()
    for kw in parsed_jd.all_skills():
        if kw.lower() in haystack:  covered.append(kw)
        else:                       missing.append(kw)
```

- 纯子串匹配，**没有任何语义输入**；
- `compute_scores` 的分数线 = `45 + coverage×55`，`coverage` 直接来自本函数（`analyze.py:667`）；
- `false_gaps` 的唯一去处是 `jd_match_agent.py:76-77` **删展示用的 findings**，而覆盖率在 `compute_scores` 里重新算一遍，**不看 findings**。

### 1.3 实测后果（`sample_resume_2_medium` × `sample_jd_backend`）

| 项 | 值 |
|---|---|
| JD 要求（`ParsedJd.all_skills()` 去重后） | 18 项 |
| 规则层判定覆盖 | 10/18 = **56%** |
| 规则层判定缺失 | `Go`、`Spring Cloud`、`Kubernetes`、`分布式`、`性能优化`、`架构设计`、`容器化`、`开源` |
| 规则分 | `overall 87 / ats 73 / impact 100 / clarity 95 / match 73 / risk 100` |

**其中 4 项是假差距**（模型判定 + 引文全部精确定位）：

| 关键词 | 简历原文证据 | `locate_quote` span |
|---|---|---|
| `分布式` | 梳理 12 个服务的调用链路，治理循环依赖与超时配置 | `[398,424]` |
| `性能优化` | 接口 P99 延迟从 320ms 降至 90ms | `[163,187]` |
| `架构设计` | 主导库存扣减接口重构，引入 Redis 预扣减与本地缓存 | `[134,162]` |
| `容器化` | Docker | `[500,506]` |

→ 覆盖率 56% → 78%，ATS 73 → 88（公式推算）。**一个做了三年分布式服务治理的候选人，被系统判为"分布式经验缺失"，ATS 占 30% 权重。**

---

## 2. 设计原则（R1 公式的落地解读）

```
① LLM 出结构化判断 + 原文证据      ← Agent 负责（§5）
② Python 硬校验                    ← locate_quote 定位失败即作废（§6.2）
③ 通过校验的要求计入"已覆盖"       ← 变成确定性数据
④ 与规则共同算分                   ← 原公式自动生效，权重不动
```

**三条纪律**

1. **LLM 在生成时参与判断，分数在计算时仍是纯函数。** 合并公式里只加 `len(verified)` 一个计数，**不引入新的打分参数**。
2. **不信任模型给出的 offset**，只信后端 `locate_quote` 的字符串定位（沿用 `services/evidence.py` 既有原则）。
3. **公式、权重、`aaa.md` 五维配比一行不改**（30/25/20/15/10）。

---

## 3. 改动总览

### 3.1 新增文件（7 个）

| 文件 | 职责 | 预估 |
|---|---|---|
| `backend/app/agent/__init__.py` | 导出 `SemanticJudgeAgent` / `TOOLS` / `run_semantic_judgment` | ~30 行 |
| `backend/app/agent/tools.py` | 8 个 tool 的 JSON Schema + 执行器（全部包装已有能力） | ~340 行 |
| `backend/app/agent/orchestrator.py` | Agent 循环、预算、收敛、降级、产出 trace | ~420 行 |
| `backend/app/agent/trace.py` | trace 记录与摘要（可观测性） | ~120 行 |
| `backend/app/agent/prompts.py` | Agent system prompt（铁律、停止条件、证据要求） | ~120 行 |
| `backend/app/agent/cache.py` | 判定结果缓存（`resume_hash + jd_hash`） | ~80 行 |
| `backend/scripts/eval_agentic.py` | 评估集与指标（假差距 recall/precision、校验通过率） | ~260 行 |

### 3.2 修改文件（7 个，全部为增量；旧调用点走默认值，行为不变）

| 文件 | 改动性质 | 风险 |
|---|---|---|
| `services/analyze.py` | 3 个函数**新增可选参数** + 覆盖率合并；公式不动 | 中（需回归） |
| `agents/base.py` | `AgentContext` 新增 1 个字段 | 极低 |
| `workflow/nodes.py` | 新增 `semantic_judge` 节点；`finalize_node` 传入校验结果 | 中 |
| `workflow/graph.py` | 新增一条可选路径（开关隔离，旧路径一行不动） | 中高 |
| `services/llm.py` | 新增 `complete_with_tools()`；现有方法不动 | 中 |
| `services/rag.py` | `search()` 新增 `role_family` 参数（默认 None=行为不变） | 低 |
| `config.py` | 新增 `workflow_mode` 等配置字段 | 极低 |

### 3.3 明确不改

`agents/` 下 8 个文件（**一行不改**）、`services/evidence.py`、`services/prompts.py`、`agents/finding_utils.py`、`services/report_render.py`、`workflow/engine.py`、`docs/api-contract.md` 的 8 步契约与报告字段、评分权重。

---

## 4. 详细改动说明

### 4.1 `services/analyze.py` —— 唯一需要动的地方（约 20 行）

**改动原则**：新增**可选**参数、默认 `None`，所有既有调用点行为逐字节不变。

```python
# 新增：校验通过的语义等价（由 §6.3 产出）
SemanticCovered = list[tuple[str, str]]   # [(keyword, quote), ...]

def _dedupe_semantic(missing, covered, semantic_covered=None):
    """语义等价去重：同一 keyword 只计一次；已被字面覆盖的不重复计。

    返回 [(keyword, quote), ...]（已通过 §6.2 硬校验的输入）。
    注：覆盖率计算见 §10.3 的 `_weighted_coverage`，本函数只负责去重。
    """
    seen, uniq = set(), []
    covered_lower = {c.lower() for c in covered}
    missing_lower = {m.lower() for m in missing}          # 只接受"字面缺失"的关键词
    for kw, q in (semantic_covered or []):
        if not kw or not q:
            continue
        k = kw.lower()
        if k in seen or k in covered_lower or k not in missing_lower:
            continue
        seen.add(k)
        uniq.append((kw, q))
    return uniq


def _tier_of(kw: str, parsed_jd: ParsedJd) -> str:
    """判断一条 JD 要求属于哪一层：core / bonus / keyword（§10）。"""
    k = (kw or "").lower()
    if any(k == s.lower() for s in parsed_jd.skills_core):  return "core"
    if any(k == s.lower() for s in parsed_jd.skills_bonus): return "bonus"
    return "keyword"


def _tier_weight(tier: str) -> float:
    return {
        "core":    settings.jd_tier_weight_core,
        "bonus":   settings.jd_tier_weight_bonus,
        "keyword": settings.jd_tier_weight_keyword,
    }.get(tier, settings.jd_tier_weight_keyword)


def _weighted_coverage(missing, covered, parsed_jd, semantic_covered=None):
    """分层加权覆盖率（§10.3）。

    - 权重取 settings（core=1.0 / bonus=0.6 / keyword=0.3），**可配置**；
    - **降级保证**：`skills_bonus` 为空时（现状）core 仍是 1.0、keyword 0.3，
      比例结构与"全相等"不同但单调性不变；若 JD 无任何分层信息，三者同权 → 退化为现状；
    - 归一化：`weighted_covered / weighted_total`，分母自归一，无需额外处理。
    """
    verified_kw = {kw.lower() for kw, _ in (semantic_covered or [])}
    hit_w = miss_w = 0.0
    for kw in covered:
        hit_w += _tier_weight(_tier_of(kw, parsed_jd))
    for kw in missing:
        if kw.lower() in verified_kw:      # 语义校验通过 → 计入命中
            hit_w += _tier_weight(_tier_of(kw, parsed_jd))
        else:
            miss_w += _tier_weight(_tier_of(kw, parsed_jd))
    total_w = hit_w + miss_w
    if total_w == 0:
        return 0.85, 0.0, 0.0                                    # 保持原兜底
    return hit_w / total_w, hit_w, total_w
```

> ⚠️ `analyze.py` 目前不导入 `settings`，需新增一行 `from ..config import settings`（无循环依赖：`config` 不引用 `services`）。

**签名改动（3 处）**

| 函数 | 行号 | 改动 |
|---|---|---|
| `ats_findings(parsed, parsed_jd, raw_text, jd_text)` | `analyze.py:136` | → `..., semantic_covered=None`；内部 `effective` 关键词从缺失清单中剔除 |
| `gap_findings(parsed_resume, parsed_jd, raw_text, jd_text)` | `analyze.py:572` | → `..., semantic_covered=None`；`analyze.py:618-634` 的覆盖率与"重合度偏低"判定改用有效值 |
| `rule_baseline_findings(...)` | `analyze.py:701` | → `..., semantic_covered=None`；传给上面两个 |

**覆盖率合并（`compute_scores`，`analyze.py:646-698`）**

```python
def compute_scores(
    findings, parsed_resume, parsed_jd, raw_text,
    semantic_covered=None,                     # ← 新增，默认 None（语义修正）
):
    # ① 剔除"关键词缺失"类发现项中已被语义校验覆盖的条目
    verified_kw = {kw.lower() for kw, _ in (semantic_covered or [])}
    scored = [
        f for f in findings
        if is_rule_origin(f) and not _is_missing_keyword_finding(f, verified_kw)
    ]
    # ② 覆盖率：分层加权（§10）+ 语义等价（§4.1）
    missing, covered = missing_keywords(parsed_resume, parsed_jd, raw_text)
    cov, hit_w, total_w = _weighted_coverage(missing, covered, parsed_jd, semantic_covered)
    coverage = cov                              # ← 原 `(len(covered)/total)` 替换为加权覆盖率
    ...                                         # 其余公式一行不改
```

> `missing_keywords`（`analyze.py:119`）**完全不动**——它的两个调用点（`ats_agent.py:25`、`jd_match_agent.py:21`）行为不变，`verify.py` 的相关断言不受影响。

**确定性入口（`compute_scores_deterministic`，`analyze.py:728-741`）**

```python
def compute_scores_deterministic(
    parsed_resume, parsed_jd, role_family, raw_text, jd_text,
    semantic_covered=None,                     # ← 新增
):
    return compute_scores(
        rule_baseline_findings(parsed_resume, parsed_jd, role_family, raw_text, jd_text,
                               semantic_covered=semantic_covered),
        parsed_resume, parsed_jd, raw_text,
        semantic_covered=semantic_covered,
    )
```

**分数输出扩展（可选，用于可解释性）**

```python
return {
    "overall": ..., "ats": ..., "impact": ..., "clarity": ..., "match": ..., "risk": ...,
    # 新增：本次由语义校验贡献的覆盖项（前端可展示"语义修正 +N"）
    "semantic_covered": [kw for kw, _ in uniq],
    "semantic_covered_count": len(uniq),
}
```

> ⚠️ 这一项会影响 `AnalysisReport` 的 `scores` 结构（`extra_json`）。**建议保留**——它是"共同算分"的可见证据；若前端严格校验字段可先不加。
> ⚠️ `uniq` 需从 `_weighted_coverage` 一并返回（或由 `compute_scores` 内部按同一规则重算一次），实现时保持**同一份去重逻辑**，不要写两份。

### 4.2 `agents/base.py` —— 新增一个字段（1 行）

```python
@dataclass
class AgentContext:
    ...
    knowledge_refs: list[dict] = field(default_factory=list)
    semantic_covered: list[tuple[str, str]] = field(default_factory=list)   # ← 新增 (keyword, quote)
```

作用：让"校验通过的语义等价"进入共享状态，`report_agent` 与 `finalize` 都能读到

### 4.3 `workflow/nodes.py`

**① 新增契约节点 `semantic_judge`**

```python
async def semantic_judge_node(state, runtime) -> dict:
    """语义判定 Agent：产出 {keyword, quote} 并自行校验；不负责算分。"""
    if settings.workflow_mode != "agentic":
        return {}                                   # 规则模式：空操作，零开销
    ctx = ctx_from_state(state)
    out = await run_semantic_judgment(ctx, runtime.context.llm)    # §5
    verified = _verify_evidence(ctx, out.data.get("judgments") or [])   # §6.2
    await record_step(task_id, "semantic_judge", status=..., tokens=..., cost=..., latency_ms=...,
                      output_digest=truncate(out.data), notes=out.notes)
    return {"semantic_covered": verified, "outputs": {"semantic_judge": out.data}}
```

**② `finalize_node` 传入校验结果**

```python
"semantic_covered" in state
    → analyze.compute_scores_deterministic(..., semantic_covered=state.get("semantic_covered") or [])
```

**③ `retrieve_node` 不变**（一次检索的既有决策保留；Agent 的按需检索走自己的 tool，见 §5.2）

### 4.4 `workflow/graph.py` —— 用开关隔离的新路径

```python
def build_graph(*, parallel=None, checkpointer=None, mode=None):
    mode = mode or settings.workflow_mode
    ...
    if mode == "agentic":
        # setup → retrieve → parse → semantic_judge → finalize → persist
        g.add_node("semantic_judge", semantic_judge_node)
        g.add_edge("parse", "semantic_judge")
        g.add_edge("semantic_judge", "finalize")
    else:
        ...   # 现有 8 节点路径，一行不动
```

**契约不破**：`workflow_steps` 仍写 8 行契约步（`semantic_judge` 产出的判断映射到 `jd_match` 步的 `output_json`，或按 `STEP_ORDER` 补齐 pending 行）；`build_graph` 末尾的 `contract_in_graph == set(CONTRACT_STEPS)` 断言保持。

同时**修正断线一**：`rg "real_gaps_as_findings"` → 统一为 `real_gaps`（`agents/jd_match_agent.py:71`）。这是 1 行改动，但**只有在 §4.1 接通后才有意义**。

### 4.5 `services/rag.py` —— 接线已死代码（约 3 行）

```python
def search(db, query, *, top_k=5, scope=None, min_score=0.05, role_family=None):   # ← 新增 role_family
    ...
    if role_family:
        stmt = stmt.where(KnowledgeDoc.role_family == role_family)
```

背景：`models.py:211` 该列**已有索引**、`rag.search` 也返回该字段，但从来没人传过。`kb_search` tool 会用它。

### 4.6 `services/llm.py` —— 新增 method，旧方法不动

```python
async def complete_with_tools(self, *, messages, tools, tool_choice="auto",
                              agent="unknown") -> LLMResult:
    """原生 function calling；复用现有供应商链/重试/计价/截断检测。"""
```

- 复用 `self.chain`、`llm_max_retries`、`compute_cost`、`finish_reason == "length"` 的截断判定；
- 返回体新增 `tool_calls`（`LLMResult` 加一个可选字段，**不改现有字段语义**）；
- 供应商不支持 `tools`（400）→ 自动降级为 **JSON 协议**（要求输出 `{"action":..,"arguments":..}`），trace 标记 `protocol="json"`。

### 4.7 `config.py` —— 新增字段（全部有默认值，不改行为）

| 字段 | 默认 | 说明 |
|---|---|---|
| `workflow_mode: str` | `"fixed"` | `fixed` / `agentic`（**默认 fixed**，用户确认） |
| `agent_max_steps: int` | `8` | 循环步数上限 |
| `agent_max_tool_calls: int` | `16` | 工具调用上限 |
| `agent_max_retrieval_rounds: int` | `2` | 检索轮次上限 |
| `agent_wall_clock_s: int` | `60` | 墙钟预算（沿用 `llm_timeout_s=60` 的量级） |
| `agent_cache_enabled: bool` | `True` | 判定结果缓存 |
| `jd_tier_tags_enabled: bool` | `True` | JD 条目分层标签（§10） |
| `jd_tier_weight_core: float` | `1.0` | 核心项权重（§10.4） |
| `jd_tier_weight_bonus: float` | `0.6` | 加分项权重（§10.4） |
| `jd_tier_weight_keyword: float` | `0.3` | 概念词权重（§10.4） |
| `scoring_version: str` | `"v2"` | 评分档位（§12） |

> **不新增**：任何"语义限幅"字段（R2 延期）。
> 分层权重**只作用于 JD 要求侧**，不得用于简历侧内容（§10.6）。

---

## 5. Agent 封装设计（核心章节）

### 5.1 类与职责

```python
# app/agent/orchestrator.py
@dataclass
class AgentRun:
    judgments: list[dict]
    trace: dict
    status: str          # done | degraded | truncated
    notes: str | None
    llm: LLMResult | None

class SemanticJudgeAgent:
    """语义判定 Agent：LLM 自主选择 tool 查证 → 提交「判断 + 原文证据」。

    唯一职责：对 JD 要求中"字面缺失"的关键词，判定它是【真差距】还是【表述差异】。
    不做的事：不算分（§4.1 负责）、不改 findings 严重度（qc 负责）、不碰合规红线。
    """
    name = "semantic_judge"
    tools = TOOLS                     # §5.2
    system_prompt = AGENT_SYSTEM      # §5.4

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentRun:
        missing, covered = analyze.missing_keywords(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        if not missing:
            return AgentRun(judgments=[], trace={"steps": 0, "tool_calls": 0}, status="done",
                            notes="无字面缺失关键词，跳过语义判定")
        # 缓存命中：同一 (resume, jd) 直接复用（temperature=0.2 → 必须缓存才可复现）
        cached = cache.get(ctx)
        if cached is not None:
            return cached
        ...  # §5.3 循环
        cache.put(ctx, run)
        return run
```

### 5.2 工具清单（8 个，全部包装已有能力）

| tool | 参数 | 底层实现（已存在） | 用途 |
|---|---|---|---|
| `jd_requirements` | `{detail?: "core"\|"bonus"\|"keywords"\|"all"}` | `ParsedJd.all_skills()` / `parsed_jd` | 取 JD 要求清单 |
| `resume_search` | `{keyword: str}` | `resume_haystack` + 原文切片（`analyze.resume_haystack`，`analyze.py:106`） | 找候选证据片段（带上下文） |
| `read_section` | `{section: "work"\|"projects"\|"skills"\|"education"}` | `ParsedResume` 结构 | 下钻某段全文 |
| **`locate_quote`** | `{quote: str, source: "resume"\|"jd"}` | `services/evidence.locate_quote` | **★ 自检工具**：这条引文能否定位 |
| `kb_search` | `{query: str, role_family?: str, top_k?: int}` | `services/rag.search`（含 §4.5 新参数） | 查等价用法/行业术语 |
| `read_chunk` | `{chunk_id: str}` | `models.KnowledgeChunk` 直读 | 看知识库切片全文 |
| `rubric_get` | `{}` | 本次任务的判定准则快照 | 工作记忆（批次内冻结） |
| `finish` | `{judgments: [...]}` | — | **提交并终止**，JSON Schema 强制结构 |

### 5.3 Agent 循环（有界、可审计）

```
state  = 只读上下文（resume_text / jd_text / parsed_* / role_family / missing[]）
trace  = AgentTrace()
messages = [system_prompt] + [user_goal(missing, resume, jd)]

for step in 1..agent_max_steps:
    resp = await llm.complete_with_tools(messages, TOOLS)     # 原生 function calling
    trace.record_assistant(resp)
    if not resp.tool_calls:
        追加一次"必须调用 finish"的强约束提示；仍失败 → 降级兜底
        break
    for call in resp.tool_calls:                              # 支持并行调用
        result = await execute_tool(call.name, call.arguments) # 引擎执行，模型不自执行
        trace.record_tool(call, result)
        messages.append(tool_result_message(call.id, result))
    if tool_calls_total >= budget or wall_clock 超限:
        trace.truncated = True; break
```

**四个 agent 要素的落点**

| 要素 | 实现 |
|---|---|
| 自主决策 | 选哪些 tool、什么顺序、是否并行、参数取值 —— **全部由模型输出**；Python 只有一个 `for` 循环，没有 if-else 决定下一步 |
| 工具使用 | 原生 `tools` + `tool_choice=auto`（**已实测**：`finish_reason=tool_calls`，一次返回 2 个并行调用） |
| 迭代 | 循环至模型调用 `finish`，或触发四个预算之一 |
| 重规划 | tool 结果回灌 messages，模型可据此改变策略（如定位失败 → 换 quote 或再检索） |

**降级（绝不静默）**

| 情况 | 行为 |
|---|---|
| 供应商不支持 tools | JSON 协议回退，trace 标记 `protocol="json"` |
| 工具执行异常 | 返回 `{"error": ...}` 回灌给模型，**由模型决定重试或换路** |
| 预算超限 | 用已有 judgments 收尾，`trace.truncated=true`，报告标注"分析提前收敛" |
| LLM 全链失败 | 复用现有 `mock_factory` 机制，`status="degraded"`，语义贡献为 0（等价于规则模式分数） |

### 5.4 `finish` 的 JSON Schema（结构化约束的落点）

```json
{
  "judgments": [
    {
      "keyword": "分布式",
      "verdict": "false_gap | real_gap",
      "resume_quote": "梳理 12 个服务的调用链路，治理循环依赖与超时配置",
      "reason": "多服务调用链路治理体现分布式系统经验",
      "self_verified": true
    }
  ]
}
```

**无论 agent 过程多自由，提交出来的永远是这个形状** —— 这就是"agentic 封装"能服务于确定性评分的原因。

---

## 6. 校验与合并（Python 侧，可信度的全部来源）

### 6.1 双层分工

| 层 | 目的 | 依据 |
|---|---|---|
| Agent 内 `locate_quote` | **提高质量**（少提交坏证据） | 模型自检 |
| 后端 §6.2 复核 | **保证可信**（不信任自称 `self_verified`） | 独立执行 |

### 6.2 硬校验规则（任一不过即作废，不报错）

1. `resume_quote` 必须能被 `evidence.locate_quote(ctx.resume_text, quote)` 定位；
2. `keyword` 必须确实在 JD 要求中（`ParsedJd.all_skills()`）；
3. 同一 `keyword` 只接受一条（防刷覆盖率）；
4. 未被字面覆盖的才计入（避免重复计数）。

```python
def _verify_evidence(ctx, judgments) -> list[tuple[str, str]]:
    required = {k.lower() for k in ctx.parsed_jd.all_skills()}
    seen, out = set(), []
    for j in judgments:
        if str(j.get("verdict")) != "false_gap":        continue
        kw = str(j.get("keyword") or "").strip()
        q  = str(j.get("resume_quote") or "").strip()
        if not kw or not q:                             continue
        if kw.lower() not in required:                  continue
        if kw.lower() in seen:                          continue
        if locate_quote(ctx.resume_text, q) is None:    continue     # ★ 硬校验
        seen.add(kw.lower()); out.append((kw, q))
    return out
```

### 6.3 实测校验通过率

真实样例上：模型判 4 条"表述差异"，`locate_quote` **4/4 全部精确定位（100%）**；判为"真差距"的 4 条（`Go`/`Spring Cloud`/`Kubernetes`/`开源`）均未编造证据。→ **`Go`、`Spring Cloud`、`Kubernetes` 不会被误判为等价。**

### 6.4 证据强度（V2.0 已落地，**默认开启**，非可选参数）

> 状态：**已实现** → `backend/app/services/semantic.py`

**为什么必须加这一层**：`locate_quote` 只能证明"引文不是编的"，**证明不了"引文够不够"**。
在 32 份排序验证夹具上，仅做可定位校验时，模型用 `Spring Cloud` 证明"具备微服务能力"、
用 `Docker` 证明"具备容器化能力"——**81 条真差距被误判为表述差异**，排序相关性由 +0.835 掉到 **+0.756（比无语义还差）**。

**规则（确定性、可解释、零新参数）**

| 规则 | 判据 | 挡住什么 |
|---|---|---|
| 出处必须是经历要点 | 引文所在行在 `工作经历`/`项目经历` 区块内，且以 `- ` 或 `* ` 起头 | 技能栏裸词（`Docker`、`Spring Cloud`） |
| 引文长度下限 | ≥ 10 字 | `MySQL`、`Redis` 这类碎片 |
| 不得跨单元格 | 引文不含 `\|`/`｜`/TAB，且与所在行同行 | 拼接出来的伪连续片段 |

**实测效果（同一批 LLM 判定，只切换这一层）**

| | 误判真差距 | 假差距 recall | Spearman |
|---|---|---|---|
| 仅可定位校验 | 81 | 100% | +0.756 |
| **+ 证据强度（生产默认）** | **48** | 90.3% | **+0.847** |
| oracle 上界 | 0 | 100% | +0.980 |

误判减少 41%，相关性由"低于基线"回到"高于基线"，recall 仅损失 9.7 个百分点。
**剩余 48 条误判的引文确实出自经历要点且长度足够，只是内容与结论不匹配——那是纯推理质量问题，规则解决不了，正是 Agent 工具循环要补的部分**（差距 +0.847 → +0.980 = **+0.133**）。

失败原因会写进 `workflow_steps.notes`（形如 `证据不足 3（too_short(6<10)×2、not_a_bullet×1）`），便于线上排查。
设计细节与逐条归因见 [P2-b 排序验证夹具与结论](P2-b_排序验证夹具与结论.md) §4。

### 6.5 校验入口

```python
# app/services/semantic.py
verify_semantic_judgments(
    resume_text, parsed_jd, missing_keywords, covered_keywords, judgments,
    require_strength=True,      # 生产默认；评估脚本可关掉以做对照
) -> (SemanticCovered, stats)
```

纯函数、不查库、不调模型；任一校验不过即作废且**不抛异常**——语义判定失败不应让整条任务失败。

---

## 7. 可观测性与落库（用户确认：两处都写）

```json
{
  "mode": "agentic",
  "steps": 5, "tool_calls": 9, "retrieval_rounds": 1,
  "self_verifications": 3, "citations_verified": 4, "citations_failed": 0,
  "semantic_covered_count": 4,
  "truncated": false, "latency_ms": 18400, "tokens": 3120, "cost": 0.021,
  "protocol": "native",
  "timeline": [
    {"step":1,"tool":"jd_requirements","args":{},"latency_ms":2,"result_digest":"core=14"},
    {"step":2,"tool":"resume_search","args":{"keyword":"分布式"},"latency_ms":3,"result_digest":"4 片段"},
    {"step":3,"tool":"locate_quote","args":{"quote":"梳理 12 个服务的调用链路","source":"resume"},"latency_ms":1,"result_digest":"span=398-424"},
    {"step":5,"tool":"finish","args":{"judgments":4,"false_gap":4},"latency_ms":0}
  ]
}
```

| 落点 | 内容 | 对应现有结构 |
|---|---|---|
| ① `workflow_steps.output_json` | 上述 trace（挂在 `semantic_judge` 对应的步骤行） | 现有表，**不加列** |
| ② `analysis_reports.extra_json.agent_trace` | 同一份 trace，供报告页展示 | 现有 JSON 列，**不加列** |
| ③ `analysis_reports.extra_json.semantic_refs` | `[{keyword, quote, span}]`（与 `knowledge_refs` 并列） | 现有 JSON 列 |

**报告页展示**（`report-view.tsx` 已有 `knowledge_refs` 区块，可平行加一块）：

```
语义修正（4 项）
├ 分布式    ← "梳理 12 个服务的调用链路，治理循环依赖与超时配置"
├ 性能优化  ← "接口 P99 延迟从 320ms 降至 90ms"
├ 架构设计  ← "主导库存扣减接口重构，引入 Redis 预扣减与本地缓存"
└ 容器化    ← "Docker"
覆盖率 56% → 84%   ｜  本次判定由 Agent 自主查证，引文均已校验（评分档位 v2）
```

---

## 8. 分阶段实施与验收

| 阶段 | 内容 | 验收（可执行） | 预估 |
|---|---|---|---|
| **P-1** | `eval_agentic.py` + 30 份标注集（人工标每条字面缺失为真/假差距） | 输出 recall / precision / 校验通过率；基线固化进仓库 | 0.5 天 |
| **P0-bonus** | 修 `parse_jd_text` 的**加分项抽取**（纯规则，无 LLM）——现状 `skills_bonus` 恒为空（§10.2） | 真实 JD 上「加分项」段落能被抽出条目（现为 0 项） | 0.5 天 |
| **P0-tier** | JD 条目分层标签（LLM 一次/JD + 缓存 + 版本化）+ 加权覆盖率接入 | `semantic_covered=None` 时加权覆盖率可单测；分层前后分数可对比 | 0.5 天 |
| **P0** | `analyze.py` 签名 + 覆盖率合并 + 缓存；**不接 LLM**（用手工构造的 semantic_covered 测） | 单测：`semantic_covered=None` 时分数与改动前**逐位相同**；给了 4 项时按 §10.5 表格 | 0.5 天 |
| **P1** | `agent/tools.py` + `llm.complete_with_tools` + `rag.search(role_family)` | 每个 tool 可直接调用且结构合规；`complete_with_tools` 拿到 `tool_calls` | 0.5 天 |
| **P2** | `orchestrator.py` + `trace.py` + `prompts.py` + `cache.py`（**单次调用 → 循环**） | 真实样例产出 trace；同一简历两次运行判定一致（缓存命中） | 2 天 |
| **P3** | 接线：`config` / `base.py` / `nodes.py` / `graph.py` / 前端开关与面板 | `mode=agentic` 走新路径；`mode=fixed` 下 `scripts/verify.py` **全绿** | 1 天 |
| **P4** | 评估对比 + 文档（含 trace 截图） | 见 §8.2 | 0.5 天 |

**共约 6 天**（P-1~P2 约 4.5 天可交付可演示版本）。

### 8.1 硬性回归断言（不可退让）

1. `semantic_covered=None` 时，`compute_scores` 输出与改动前**逐位相同**；
2. `mode=fixed` 时，`scripts/verify.py` 174 项全绿；
3. 同一份简历 + 同一 JD，**两次诊断分数逐位相同**（靠 §4.1 纯函数 + `cache.py` 保证）；
4. 8 步 `workflow_steps` 契约与 `docs/api-contract.md:240` 不变；
5. 合规：`qc_agent._check` 规则四查**照常执行**，语义判定不得绕过。

### 8.2 评估指标（P4）

| 指标 | 规则模式基线 | 目标 |
|---|---|---|
| 假差距识别率（recall） | **0%**（机制断线） | ≥80% |
| 证据校验通过率 | — | ≥85%（实测样例 100%） |
| 精度（precision，不把真差距判成等价） | — | ≥90% |
| 跨语种/同义简历分数虚低 | 实测 −15 分（ATS 73 应为 88） | ≤3 分 |
| **加分项抽取条数** | **0 项**（`skills_bonus=[]`） | ≥1 项（真实 JD 上） |
| **分层对排序的影响** | 无（18 项等权） | 强候选人不再因缺加分项被重罚（Spearman ↑） |
| 同输入重跑一致性 | 100% | **仍 100%** |
| 成本增量 | — | ≤¥0.01/份（实测单次 ¥0.0042） |
| 延迟增量 | 基线 107–130s | ≤+10s（实测单次 7.4s） |

---

## 9. R2 / R3 的延期说明（按用户要求）

| 项 | 是否引入 | 理由 | 再次评估的条件 |
|---|---|---|---|
| **R2 语义限幅** | ❌ 不引入 | R1 的硬校验（引文必须可定位）本身就是约束；覆盖率有上限（关键词总数固定 + `_clamp`） | P-1 评估显示 precision < 90%，或出现"单维度被顶到饱和" |
| **R3 权重配比** | ❌ 不引入 | 合并公式只加一个计数，不引入"规则:语义"的比例概念 | P-1 显示语义贡献过强/过弱（如 recall 高但排序相关性下降） |

**延期不等于遗忘**：两者都是 `analyze.py` 内的局部改动，任何时候加都不需要动架构。

---

## 10. 分层加权：要求的重要性不一样（用户提出，已采纳）

### 10.1 为什么需要它（第二个断线）

`compute_scores` 的覆盖率对 **`ParsedJd.all_skills()` 返回的 18 项一视同仁**：

```python
# analyze.py:119-133（逐字）—— 没有 tier 概念
for kw in required:
    if kw.lower() in haystack: covered.append(kw)
    else:                      missing.append(kw)
```

→ **简历缺一个"加分项"，与缺一个"核心要求"，扣分完全相同**。这是规则层的第二个错配，与"假差距"是两件不同的事。

### 10.2 前置缺陷：加分项结构建好了，但抽不出来（实测）

```python
# parse_rules.py:401-403 —— 加分项本来有专门一层
RESP_HEADINGS  = ["岗位职责", "工作职责", ...]
REQ_HEADINGS   = ["任职要求", "岗位要求", ...]
BONUS_HEADINGS = ["加分项", "优先考虑", ...]

# parse_rules.py:479-480
parsed.skills_core  = [t for t in matched if t not in bonus_matched][:25]
parsed.skills_bonus = [t for t in matched if t in bonus_matched][:15]
```

实测 `sample_jd_backend`：

```
skills_core  : Java, Go, Spring, Spring Boot, Spring Cloud, MySQL, Redis, Kafka,
               Kubernetes, 微服务, 分布式, 高并发, 性能优化, SQL       （14 项）
skills_bonus : []        ← 空！
keywords     : 高并发, 分布式, 微服务, 性能优化, 稳定性, 架构设计, 容器化, 开源  （8 项）
```

JD 原文写着「加分项：1. 有开源项目贡献经验 2. 大规模交易系统稳定性建设经验 3. 熟悉 Service Mesh 与云原生技术栈」，**但 `matched` token 表里没有这些词，一项都没抽出来**。

> **结论：分层的第一步不是接 LLM，而是修 `parse_jd_text` 的加分项抽取（P0-bonus，纯规则）。** 抽不出来，权重就没有作用对象。

### 10.3 分层与权重

| 层 | 来源 | 权重（配置项，默认） |
|---|---|---|
| `core` | `ParsedJd.skills_core` | `1.0` |
| `bonus` | `ParsedJd.skills_bonus` | `0.6` |
| `keyword` | `ParsedJd.keywords` | `0.3` |

加权覆盖率：

```
coverage = Σ(命中项权重) / Σ(全部项权重)          ← 分母自归一，无需额外处理
```

**为什么不是 0.6 / 0.4**：两个权重越接近，分层越不起作用。0.6/0.4 只表达"重要性 1.5 倍"，而"缺加分项"与"缺核心项"的实际差距远大于 1.5 倍。用 1.0/0.6 表达 1.67 倍；`keyword` 层（`稳定性`/`架构设计`/`容器化`/`开源` 这类从职责描述抽出的概念词）价值最低，取 0.3。

### 10.4 实测效果（同一份真实样例）

`sample_jd_backend` 的实际权重总量：core 14 项 ×1.0 = 14.0；bonus 0 项（§10.2 缺陷）；keywords 4 项非重叠 ×0.3 = 1.2 → **总权重 15.2**（`微服务`/`分布式`/`高并发`/`性能优化` 已在 core 中，按 §10.5 边界规则不重复计权）。

| 方案 | 加权覆盖 | ATS |
|---|---|---|
| 现状（18 项等权） | 10/18 = 56% | **73** |
| **仅分层加权**（core 1.0 / bonus 0.6 / keyword 0.3） | 8.6/15.2 = **57%** | **76** |
| **分层 + 语义修正**（4 项等价） | 12.7/15.2 = **84%** | **91**（仅按公式推算） |
| 参考：仅语义修正（等权口径） | 14/18 = 78% | 88 |

> ⚠️ 上表为**手工推算**，用于说明量级；准确数值由 P0 单测锁定（§8.1）。注意"仅分层"这一版会因为 `keywords` 进了分母（权重 0.3）而略微压低覆盖率——**这也是 §10.5「keywords 是否该进分母」需要评估数据来定的原因**。

**分层与语义修正的不是同一批问题，可以叠加**：分层修正"权重错配"，语义修正"假差距"。上表还显示一个反直觉结果：**加了 semantic_covered 后，分层版反超等权版**——因为被语义救回的 4 项里正好有 core 项（`分布式`/`性能优化` 在 core 中，权重 1.0），而它们的分母占比更高。这也说明两处改动**必须一起评估**，否则会得出错误结论。

### 10.5 实现要点

| 要点 | 做法 |
|---|---|
| 标签来源 | 确定性优先：先信 `parse_jd_text` 的 `skills_core` / `skills_bonus`；LLM 只在**抽取不完整时**补充，并同样按 §6.2 思路做枚举校验 |
| 缓存与版本化 | 按 `jd_hash` 缓存标签 + `rubric_version`；**一个 JD 跑一批简历，标签一次算出、全批复用** → 批次内所有人被同一把尺子量 |
| 降级保证 | `skills_bonus` 为空时（现状）core=1.0、keyword=0.3 仍成比例，公式自洽；JD 无任何分层信息 → 同权，退化为现状（可由单测锁定） |
| 边界 | 同一词同时出现在 core 与 keywords（本例 `微服务`/`分布式`/`高并发`/`性能优化`）→ 按 `core` 优先取一层，**不重复计权** |
| 作用域 | **只作用于 JD 要求侧**，绝不作用于简历侧内容（见 §10.6） |

### 10.6 ⚠️ 单位错配（写死这条，防止实现串味）

"核心 0.6 / 加分 0.4"这类系数**只对 JD 要求侧成立**。绝不能把它套到**简历侧的要点/经历**上（例如"量化成果算核心项"）——简历内容没有"核心/加分"这个维度，用 JD 侧的重要性系数去乘简历内容没有任何依据。**分层只影响覆盖率的分子分母，不产生新的评分维度。**

### 10.7 验收

1. `sample_jd_backend` 上 `skills_bonus` 从 **0 项 → ≥1 项**；
2. 加权覆盖率在"无分层信息"输入下与改动前**逐位相同**；
3. 分层前后同一批简历的排序变化可解释（缺加分项的候选人不再被重罚）；
4. 标签缓存命中时，同一 JD 的多次运行标签完全一致。

---

## 11. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 模型不调用 `finish` 只出文本 | 循环空转 | 强约束 prompt + 一次 `tool_choice` 指定 `finish` + JSON 协议回退 |
| 模型编造引文 | 分数虚高 | **硬校验**：`locate_quote` 定位失败即作废（实测 100% 拦截） |
| `temperature=0.2` 导致重跑微差 | 同简历两次分数不同 | `cache.py`：`resume_hash + jd_hash` 为键，命中即复用 |
| 缓存与简历更新不一致 | 用旧判定 | 键含 `resume_hash`（原文摘要）；简历改动 → hash 变化 → 自动失效 |
| 改动 `analyze.py` 破坏既有断言 | 174 项失败 | 所有新参数默认 `None`；P0 先跑"逐位相同"单测再往下走 |
| 报告页新增字段破坏前端 | 白屏 | `semantic_refs`/`semantic_covered` 均为**可选字段**，前端按缺省容忍 |
| HR 批量成本上升 | 超预算 | `hr_screening` 仍走 `fixed`；智能模式仅单份诊断开放 |

---

## 12. 评分档位 `score_v2`（决策 b，已确认）

### 12.1 为什么需要档位

语义修正与分层加权都会改变**绝对分数**（本例 ATS 73 → 78 → 87、总分 87 → 约 92）。若不区分档位：

- 历史报告与新报告不可比——用户会看到"同一份简历，上周 73 这周 88"，第一反应是系统不稳定；
- HR 排序表跨批次比较失去基准；
- 一旦发现语义判定有问题，**无法回滚**。

### 12.2 设计

```
score_v1 = 现有公式（子串覆盖率 + 等权）          ← 保留，永不改动
score_v2 = 加权覆盖率 + 语义校验                 ← 新增档位

分析任务 / 报告：analysis_reports.extra_json.scoring_version = "v1" | "v2"
                  （同时把 rubric_version、模型名一并写入，便于对账）
前端：默认展示 v2；报告页标注"评分档位 v2"
HR 排序表：批次内统一使用同一档位，禁止混排
```

### 12.3 实现要点

| 要点 | 做法 |
|---|---|
| 开关 | `settings.scoring_version`（默认 `v2`），并可被 `POST /tasks` 的 `mode` 覆盖 |
| 双档可选 | `compute_scores(...)` 内部按档位分支：v1 走原逻辑（等权、无语义），v2 走 §4.1 + §10.3 |
| 单测锚点 | **v1 分支必须与改动前逐位相同**——这是"零回归"的硬断言（§8.1 第 1 条） |
| 历史数据 | 已有报告的 `scoring_version` 缺失 → 视为 `v1`，不做数据迁移 |
| 导出与分享 | Markdown/PDF 导出带上档位标注（`report_render.py` 一处小改） |

### 12.4 与 `workflow_mode` 的关系（两个开关正交）

| 开关 | 管什么 | 取值 |
|---|---|---|
| `workflow_mode` | **谁来判定**：固定流水线 / Agent 自主工具循环 | `fixed` \| `agentic` |
| `scoring_version` | **分数怎么算**：等权子串 / 加权 + 语义 | `v1` \| `v2` |

四种组合都合法，其中两个是重点：

| 组合 | 含义 | 用途 |
|---|---|---|
| `fixed` + `v1` | 现状 | 基线、回归对照 |
| `fixed` + `v2` | 语义判定走**单次调用**，分数走新档位 | **P1/P2 的对照实验臂**（证明 agent 循环是否必要） |
| `agentic` + `v2` | 完整智能模式 | 目标形态 |
| `agentic` + `v1` | Agent 查证只用于展示，不计分 | 调试/降级 |

> 这个正交关系让"agentic 是否值得"的实验变量保持单一：**固定 `v2`，只切换 `fixed`/`agentic`**（见总览文档的实验 2）。

---

## 13. 待确认事项

| # | 事项 | 结论 |
|---|---|---|
| 1 | 分数变更方式：直接改现有公式（a）/ 新档位 `score_v2`（b） | ✅ **b（已确认）**，见 §12 |
| 2 | `scores` 是否新增 `semantic_covered` 字段（前端可展示"语义修正 +N"） | 建议加（用户未反对） |
| 3 | 智能模式是否允许跳过某些规则检查 | ✅ 允许（已确认），但 `qc` 规则四查与分数计算不可跳 |
| 4 | `semantic_judge` 做第 9 契约步还是映射进 `jd_match` 步 | 建议**映射**（零契约变更） |
| 5 | 分层权重取值（core/bonus/keyword） | 建议 `1.0 / 0.6 / 0.3`，做成配置项（§10.3） |
| 6 | 是否接受工期从 5 天调整为 **6 天**（新增 P0-bonus、P0-tier） | 待确认 |

**确认后即可开工，第一步为 P-1（30 份标注集），其次 P0-bonus（修加分项抽取）。**
