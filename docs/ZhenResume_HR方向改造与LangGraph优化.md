# ZhenResume 转向 HR 侧：改造清单 × LangGraph 优化方案

> 前提：基本功能已完成（mock 174/174；live 实测 171/174、147 秒/份、¥0.065/份）。
> 本文回答两件事：**① 侧重点转到 HR 之后，做什么、改什么、放弃什么；② LangGraph 具体怎么落地这些优化。**
> 本文会**修订**上一份文档里的一个结论（HR 批量原本建议"不用 Send"），原因写在 §8。

---

## 1. 定位转变：从"帮求职者改简历"改成"帮 HR 初筛候选人"

这两件事**共用同一套底层能力**（解析、JD 匹配、风险检测、证据链、公平性），但**要的东西几乎相反**：

| 维度 | 求职者侧（现状主线） | HR 侧（新的侧重点） |
|---|---|---|
| 核心问题 | "我这份简历怎么改" | "这 100 个人里谁值得先看" |
| 主要产出 | **改写句**、逐条建议、action_items | **排序表**、匹配证据、风险提示、简版报告 |
| 单位 | 1 份简历 | **1 个 JD × N 份简历** |
| 关键指标 | 报告可用性、二次诊断率 | **吞吐（100 份 ≤10 分钟）**、排序可解释、合规 |
| 成本敏感度 | 低（单份 ≤0.5 元） | **高**（一份 JD 就是 100–500 次分析） |
| 失败后果 | 用户重试 | 一批 100 人白跑、HR 不再用 |
| 合规 | 公平性（不歧视求职者） | 公平性 + **审计**（谁在什么时候用什么依据筛掉了谁） |
| 现有收入设计 | 9.9 / 19.9 / 39 / 99 元 | **999 / 2999 元/月**（`plans.py` 已有，只差实现） |

**关键判断：现在的 HR 批量是把"求职者报告"按份数批处理了一遍，而不是一个 HR 产品。** 证据：
`api/hr.py:72` 对每份简历调用的是**同一条 8 节点求职者流水线** —— 其中 `impact`（量化改写）、`suggestion`（≥5 条改写建议 + action_items）、`report`（写给求职者的总结）**对 HR 基本无用**，却占了约一半的调用次数与延迟。

### 1.1 值得做的（按优先级）

1. **HR 专用筛选子图**（砍掉 3–4 个求职者节点）→ 直接解决吞吐与成本；
2. **批次级并发 + 断点续跑**（100 份串行 4.1 小时 → 目标 ≤10 分钟）；
3. **HR 视角的排序与批次页**：匹配证据、缺失项、风险提示、面试问题，**不给 HR 改写句**；
4. **去重**（同一人多次投递 / 猎头重复推）；
5. **合规三件套**：公平性闸门（歧视性依据不许出分）+ 审计日志 + 排名可复现。

### 1.2 值得"冻结"的（不删，但不投人力）

- 简历编辑器 `/editor`、4 套模板、二次诊断对比 —— 保留为**免费引流工具**，是 HR 侧的获客入口；
- 支付/登录 —— 已决定暂缓（见优化路线图）。

---

## 2. HR 侧现状与差距（实测口径）

| 项 | 现状 | 目标 | 差距 |
|---|---|---|---|
| 吞吐 | 串行 for 循环（`api/hr.py:53-72`）→ **100 份 ≈ 4.1 小时**（按 live 实测 147 s/份） | 100 份 ≤ **10 分钟**（`aaa.md §4`） | **25×** |
| 每份成本 | 跑完整 8 节点 ≈ ¥0.065 | 100 份 ≤ ¥3（≈¥0.03/份） | 2× |
| 每份产出 | 全套求职者报告（含改写建议） | 匹配证据 + 风险 + 简版摘要 | 产出错位 |
| 排名依据 | `overall_score`（求职者五维加权，含"清晰度"） | 应以 **JD 匹配 + 硬性条件闸门** 为主 | 依据不对 |
| 排名可复现 | 未验证（live 下分数会抖，见优化路线图 §0.4） | 可复现 + 可解释 | 未达标 |
| 配额 | **完全不校验**（`hr.py:1-9` 自述） | hr_basic 100 份/月、hr_pro 500 份/月 | 无闸门 |
| 断点续跑 | 无：进程挂了整批从头 | 候选人级续跑 | 无 |
| 进度反馈 | 前端轮询（逐条落库，这点是对的） | 保留 + 流式 | 可用 |
| 批次字段 | 只有 overall/match/risk/summary | 缺：分维度、关键证据、排名、裁决、备注 | 缺字段 |
| 合规 | 双层歧视拦截 + fairness 统计 | 加：**审计日志** + **歧视性依据不许出分** | 半成品 |

数据来源：`api/hr.py`、`HrBatchItem`（`models.py:312-327`）、`plans.py:54-71`、`aaa.md §3.9/§4`、`docs/api-contract.md §6.5`，以及本轮 live 实测。

---

## 3. LangGraph 怎么优化这个项目（7 个机制，逐个对应 HR 的真痛点）

> 一句话：**求职者侧用 LangGraph 是为了"质量与可恢复"，HR 侧用 LangGraph 是为了"吞吐、批次语义与合规"。**
> 下面每条都写清"对应哪个痛点 + 图上怎么落"。

### 3.1 批次 = map-reduce 图（Send 扇出），替掉串行 for 循环

**痛点**：现状 100 份串行 4.1 小时；且一个候选人卡住会带停整批。

**图上怎么落**：

```
START → plan（载入 JD + 候选人清单）
      → dispatch ──(条件边返回 list[Send])──► screen_one ×N   ← 同一 superstep 并发
      → screen_one → wave_done（等齐这一波）
      → wave_done ──(还有剩余?)──► dispatch（下一波）
                    └─(没有)────► dedup → rank → fairness_gate → persist → END
```

- `Send("screen_one", {...})` 从**条件边**返回，payload 里带 `{batch_id, item_id, jd, resume_id}`；
- `screen_one` 的产出用 reducer 累积（`results: Annotated[list, operator.add]`）；
- **分波（wave）而不是一次扇出 100 个**：`HR_BATCH_WAVE=10`。理由：一次 100 个 asyncio 任务会同时持有 100 个 DB 会话/httpx client，且会在瞬间打爆模型限流；分波还能让每一波结束就落库，前端看到表格逐段填满。

### 3.2 候选人级子图：砍掉求职者专用节点（**吞吐与成本的最大杠杆**）

**痛点**：HR 不需要改写句，却在为它付费等待。

**图上怎么落**：`screen_one` 不是一个节点，而是一个**子图**：

```
screen_one 子图： setup → parse(可缓存) → jd_match → risk → hr_score → hr_summary → END
```

| 节点 | 求职者流水线 | HR 子图 | 理由 |
|---|---|---|---|
| parse | ✅ | ✅（复用 `resumes.parsed_json`，跨 JD 不重复调） | 必需 |
| jd_match | ✅ | ✅ | HR 的核心：匹配与差距 |
| risk | ✅ | ✅ | HR 的核心：风险/合规 |
| ats | ✅ | 可选（建议保留） | 能提示"这份简历机器读不了" |
| impact | ✅ | ❌ | 量化改写是给求职者的 |
| suggestion | ✅ | ❌ | ≥5 条改写建议对 HR 无用，且是**最慢的节点（46 s / 2212 token）** |
| report | ✅ | ❌（换成 `hr_summary`） | 总结口径不同 |
| qc | ✅ | ✅（轻量） | 证据可定位 + 歧视拦截必须留在 HR 侧 |

**收益估算**（基于本轮 live 实测的逐节点耗时）：

| 方案 | 单份耗时 | 100 份串行 | 100 份并发 8 | 单份成本 |
|---|---|---|---|---|
| 现状（8 节点，串行批处理） | 147 s | 4.1 h | 30 min | ¥0.065 |
| HR 子图（5 节点） | ~75 s | 2.1 h | 16 min | ~¥0.040 |
| + 快模型路由（`deepseek-v4-pro-0813`，实测快 1.8×） | ~42 s | 1.2 h | 9 min | ~¥0.030 |
| + 输出瘦身（`LLM_MAX_TOKENS`↓、提示词限条数） | ~35 s | 1.0 h | **7.5 min** | ~¥0.025 |

> **结论：100 份 ≤10 分钟要三件事同时成立 —— 子图裁剪 + 快模型路由 + 并发 8。** 只做并发（现状节点数不变）是 30 分钟，达不到验收线。

### 3.3 checkpoint 粒度 = 候选人级 → 批次可续跑

**痛点**：批次跑 10 分钟，中途崩了现在要从第 1 个人重来。

**图上怎么落**：
- 每个候选人一个 `thread_id = f"hr:{batch_id}:{item_id}"`；批次本身用一个轻量进度（`HrBatch.finished` 已有）。
- 优点：`wave_done` 之前已完成的候选人**不会重跑**（LangGraph 的 pending writes 保证同一步里成功的兄弟不重跑）；
- 进程重启后从 `pending` 里挑 `status='pending'` 的候选人继续（复用优化路线图里的僵尸回收思路，只是粒度从 task 变成 batch item）。

### 3.4 `interrupt()` 做人工复核 / 短名单（**HR 产品的差异化功能**）

**痛点**：HR 的流程不是"出个分"，而是 初筛 → 人工过一遍 → 面试 / 淘汰 / 待定。现状没有任何裁决概念。

**图上怎么落**：

```
... → fairness_gate → shortlist_gate ──(interrupt: 等 HR 裁决)──► finalize → END
```

- `interrupt({"batch_id":..., "candidates":[...top N 及理由...]}, response_schema=ShortlistDecision)`（1.2.12+ 支持类型化表单）；
- HR 在前端勾选"进面试/待定/淘汰 + 备注" → 后端 `Command(resume={...})` 续跑；
- **注意 `interrupt` 的重放语义**：resume 时整个节点函数从头重跑，所以 `interrupt()` **之前**的写库必须幂等（用 upsert），或把落库放到 `interrupt()` 之后；
- 需要新增端点（**先改 `docs/api-contract.md`**）：`POST /api/v1/hr/batches/{id}/decisions`。

### 3.5 公平性与"不许歧视"变成图上的**强制闸门**

**痛点**：HR 场景下"筛掉人"的合规风险远高于求职者场景。

**图上怎么落**：`fairness_gate` 节点（reduce 之前，逐候选人判定）：

- 任何候选人的 findings 里出现性别/年龄/婚育/户籍等依据 → **该候选人不出分数**，只输出"需人工复核 + 原因"；
- 批次级输出 `fairness_pass_rate`（复用现有 `qc_agent` 的统计口径）；
- 现在 `qc_agent` 只是"降级/剔除条目"，HR 场景要更强：**歧视性依据不允许参与排名**。

### 3.6 去重与缓存（HR 场景独有）

**痛点**：同一人多次投递、猎头重复推、同一份简历投多个 JD。

**图上怎么落**：
- `dedup` 节点：简历正文归一化后取指纹（`sha256`）→ 完全重复的直接合并；可选 embedding 近邻做"疑似同一人"提示；
- `parse` 节点按 `resume_id` **命中缓存**（`resumes.parsed_json` 已存在）→ 同一份简历投 3 个 JD 时省 3 次 parse；
- 去重发生在 `reduce` 前，避免排名表里出现两份一样的。

### 3.7 流式进度：让 HR 不用干等 10 分钟

**痛点**：现状是 1.5s 轮询，虽然逐条落库、观感尚可，但没有任何"推"的能力。

**图上怎么落**：
- 短路径：保持轮询，但**每完成一个候选人就单独提交事务**（现状 `_process_batch` 已经是每 item 一个 `session_scope`，这个设计要保住）；
- 长路径（可选）：`astream(stream_mode="updates")` → 把每个候选人完成事件转成 SSE，前端从"表格逐行出现"升级为"实时推送 + 进度条"；
- **不要**为了流式去改子图结构 —— 流式只是把已有的逐条落库事件转发出去。

---

## 4. 目标架构：两张图、一套服务

```
                         ┌──────────────── FastAPI 契约层（对外不变的地方尽量不变）────────────────┐
POST /hr/batches  ──202──►│ hr_batches / hr_batch_items（+ 新增裁决/排名/证据字段）              │
GET  /hr/batches/{id} ◄───│ 轮询：逐条落库 → 表格逐行填满                                        │
POST /hr/batches/{id}/decisions（新增）→ Command(resume=裁决)                                │
                         └──────────────────────────────┬──────────────────────────────────────┘
                                                        │
                        ┌───────────────────────────────▼──────────────────────────────────────┐
                        │ HR 批量图（LangGraph，新）                                           │
                        │  plan → dispatch ─Send×wave─► screen_one（子图）→ wave_done → loop    │
                        │                                        ↓                             │
                        │            dedup → rank → fairness_gate → shortlist(interrupt) → END │
                        └───────────────────────────────┬──────────────────────────────────────┘
                                                        │ 复用同一套 services/*
                        ┌───────────────────────────────▼──────────────────────────────────────┐
                        │ 求职者单份图（已有方案，保留）                                        │
                        │  parse → jd_match → ats/impact/risk → suggestion → report → qc        │
                        └──────────────────────────────────────────────────────────────────────┘
```

**关键：两张图共享 `services/*` 与 8 个 Agent 文件（零改动），HR 子图只是"少挂几个节点 + 换一个汇总节点"。**

---

## 5. 关键代码骨架

### 5.1 HR 批量图（波浪式 map-reduce）

```python
# backend/app/workflow/hr_graph.py（新增）
from __future__ import annotations

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..config import settings


class BatchState(TypedDict, total=False):
    batch_id: str
    jd: dict
    pending: list[str]                       # 还没筛的 hr_batch_items.id
    wave: int
    results: Annotated[list, operator.add]   # 每个候选人一条
    ranking: list
    decisions: dict
    fairness: dict


def plan_node(state: BatchState) -> dict:
    """载入 JD 与候选人清单（只读）。"""
    ...


def dispatch(state: BatchState) -> list[Send]:
    """把这一波的候选人扇出成并发子任务。**只能从条件边返回 Send**。"""
    wave = state["pending"][: settings.hr_batch_wave]
    return [
        Send("screen_one", {
            "batch_id": state["batch_id"],
            "item_id": item_id,
            "jd": state["jd"],
            "wave": state.get("wave", 0),
        })
        for item_id in wave
    ]


async def screen_one_node(state: dict, runtime) -> dict:
    """单个候选人：跑 HR 子图 + 立刻落库（让前端逐行看到结果）。"""
    result = await run_screening_subgraph(state, runtime.context)
    await upsert_batch_item(result)          # 幂等：重放安全
    return {"results": [result]}


def wave_done(state: BatchState) -> dict:
    """收波：把已完成的候选人从 pending 里摘掉。"""
    done = {r["item_id"] for r in state.get("results", [])}
    return {"pending": [i for i in state["pending"] if i not in done],
            "wave": int(state.get("wave", 0)) + 1}


def after_wave(state: BatchState) -> str:
    """还有剩余 → 再扇一波；都完成 → 去重排序。"""
    return "dispatch" if state["pending"] else "dedup"


def build_hr_graph(*, checkpointer=None):
    g = StateGraph(BatchState, context_schema=GraphContext)
    g.add_node("plan", plan_node)
    g.add_node("screen_one", screen_one_node)     # 子图入口
    g.add_node("wave_done", wave_done)
    g.add_node("dedup", dedup_node)
    g.add_node("rank", rank_node)
    g.add_node("fairness_gate", fairness_gate_node)
    g.add_node("shortlist", shortlist_node)       # 内含 interrupt()，可开关
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", dispatch, ["screen_one"])
    g.add_edge("screen_one", "wave_done")
    g.add_conditional_edges("wave_done", after_wave, {"dispatch": "plan", "dedup": "dedup"})
    g.add_edge("dedup", "rank")
    g.add_edge("rank", "fairness_gate")
    g.add_edge("fairness_gate", "shortlist" if settings.hr_shortlist_enabled else "finalize")
    g.add_edge("shortlist", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
```

> 注意 `after_wave` 回到 `plan` 会让 `plan` 重跑（幂等、只读，成本可忽略），或者拆一个 `next_wave` 空节点更直观。

### 5.2 HR 筛选子图（节点裁剪，复用现有 Agent）

```python
HR_NODES = ("parse", "jd_match", "risk", "hr_score", "hr_summary")   # ats 可选

def build_screening_subgraph():
    """与求职者图共用 Agent 工厂，只是少挂节点、换汇总节点。"""
    g = StateGraph(WorkflowState, context_schema=GraphContext)
    nodes = build_agent_nodes()
    for name in ("parse", "jd_match", "risk"):
        g.add_node(name, nodes[name])
    g.add_node("hr_score", hr_score_node)       # 确定性：只吃规则信号，保证排名可复现
    g.add_node("hr_summary", hr_summary_node)   # LLM：匹配证据 + 缺失项 + 面试问题
    g.add_edge(START, "setup"); g.add_edge("setup", "parse")
    g.add_edge("parse", "jd_match"); g.add_edge("jd_match", "risk")
    g.add_edge("risk", "hr_score"); g.add_edge("hr_score", "hr_summary")
    g.add_edge("hr_summary", END)
    return g.compile(checkpointer=None)   # 子图不单独 checkpoint，由外层 thread 管
```

**`hr_score` 必须是确定性的**（否则排名会因为模型波动而抖动，HR 无法解释"为什么上周 A 第一这周 B 第一"）：

```python
def hr_score_node(state) -> dict:
    """HR 排名分：只吃规则层产出的确定性信号。

    - 硬性条件（年限/学历/必备技能）→ 不满足则 gate = "人工确认"，不直接淘汰；
    - 匹配分用 analyze.missing_keywords 的覆盖率；
    - 风险分用规则层 time-line/夸大/合规检测，**不用 LLM 补充条目**（那会让分数不可复现）。
    """
    ctx = ctx_from_state(state)
    deterministic = [f for f in ctx.findings if f.get("deterministic", True)]
    ...
    return {"scores": {...}, "ranking_key": {...}}
```

> 这正是优化路线图 §0.4 第 3 条（分数不可复现）在 HR 场景下的**必然要求**：求职者侧是"卖点失真"，HR 侧是"排名不可信"。

### 5.3 并发限流（必须，否则一次扇出会打爆限流）

```python
context = GraphContext(
    llm=get_llm(),
    retriever=rag.get_retriever(),
    semaphore=asyncio.Semaphore(settings.hr_llm_concurrency),   # 默认 8
)
```

- `hr_batch_wave=10` 控制"同时有多少候选人在跑"；
- `hr_llm_concurrency=8` 控制"同时在飞的 LLM 调用数"；
- 两者要一起看：wave 决定内存与 DB 会话数，semaphore 决定 API 限流压力。

---

## 6. 数据模型与契约改动（HR 侧）

### 6.1 `HrBatchItem` 需要补的字段（加列，不改现有列）

| 字段 | 用途 |
|---|---|
| `ranking_key` / `rank` | 排名依据与名次（可复现、可解释） |
| `match_evidence` (JSON) | 命中的 JD 要求 + 引文（HR 最想看的东西） |
| `gaps` (JSON) | JD 有、简历无的项 |
| `risk_items` (JSON) | 风险点（时间线/夸大/合规） |
| `interview_questions` (JSON) | 面试问题（LLM 生成，HR 侧独有） |
| `decision` / `decided_at` / `notes` | 人工裁决（进面试/待定/淘汰）与备注 |
| `dedup_group` | 去重分组（同一人多次投递） |
| `fairness_blocked` (bool) + `fairness_reason` | 被合规闸门拦下（不参与排名） |

### 6.2 契约改动（先改 `docs/api-contract.md` 再写代码）

- `GET /hr/batches/{id}`：`items[]` 增加上述字段；新增 `fairness` 汇总与 `dedup_groups`；
- **新增** `POST /hr/batches/{id}/decisions`：提交人工裁决（`interrupt` 的 resume 入口）；
- `GET /hr/batches/{id}/export.csv`：列从 8 列扩到"名次/匹配分/风险/命中要求/缺失项/风险点/裁决"；
- 配额：`POST /hr/batches` 要按 `plans.py` 的 `hr_basic=100 / hr_pro=500` 校验（现状完全不校验）。

---

## 7. 分阶段落地与验收

| 阶段 | 内容 | 验收 | 人日 |
|---|---|---|---|
| **H0** | 先修优化路线图 §0.4 的 3 个 live 失败（分数可复现 / 关键词引文 / 空窗检测） | live 下 174/174 | 2 |
| **H1** | HR 子图（砍节点 + `hr_score` 确定性 + `hr_summary`） | 单份耗时 147s → ≤75s；成本 ≤¥0.04；HR 报告含匹配证据/缺失/面试问题 | 3 |
| **H2** | 批次图 + 波浪并发 + 候选人级 checkpoint | **100 份 ≤10 分钟**；kill 进程后重启只补跑未完成的人 | 4–5 |
| **H3** | `HrBatchItem` 字段 + 排名/导出改造 + 配额闸门 | CSV 含命中要求与缺失项；免费用户批量被拒 | 3 |
| **H4** | 去重 + 合规闸门 + 审计日志 | 重复投递合并；歧视性依据的候选人不参与排名；audit_logs 有记录 | 3 |
| **H5** | 人工裁决（`interrupt` + 新端点 + 前端勾选） | 提交裁决后批次状态正确流转；`interrupt` 前副作用幂等 | 4 |

**总计约 19–22 人日**，其中 H0+H1+H2 是"HR 能不能用"的门槛（≈10 人日）。

---

## 8. 修订：上一份文档里的一个结论要改

在 [ZhenResume_LangGraph接入与并发实现.md](ZhenResume_LangGraph接入与并发实现.md) §3.3 我写过：

> "HR 批量用 `asyncio.Semaphore` + 独立任务（形态三），**不需要** LangGraph 的 `Send`，因为各份之间零共享状态。"

**这个结论只在"单纯追求吞吐"时成立。** 侧重点转到 HR 之后它不成立了，因为 HR 批次有两个**必须的批次级阶段**：

1. **reduce 阶段**：去重、排名、合规闸门、批次级公平性统计 —— 这些要看**全部候选人**才能算，不是"各份独立"；
2. **人工裁决阶段**：`interrupt` 挂起整批等 HR 决策 —— 这是批次级状态，不是候选人级。

所以正确形态是：**用批次图（Send 扇出 + wave 分波）包住候选人子图**，而不是简单 `gather`。形态三仍然适用于"与批次无关的批量补数据"这类场景（例如批量重建知识库索引）。

---

## 9. 除了 LangGraph，HR 方向还必须补的三件事

1. **审计日志**（`AuditLog` 表已建、从未写入）：HR 场景下"谁在什么时候用什么依据筛掉了谁"必须可查 —— 这既是合规要求，也是 HR 内部复盘的依据。
2. **简历的保留与删除策略**：HR 上传的是**第三方**简历（候选人并未同意给这个系统处理）。至少要做到：批次删除 → 简历与文件级联物理删除（`DELETE /resumes/{id}` 已有）、可配置保留期、导出留痕。这是 HR 采购时一定会问的问题。
3. **"简版报告"的独立口径**（`aaa.md §3.9` 明确要求）：不要复用求职者报告的字段硬改 —— 用 `hr_summary` 节点输出 HR 关心的四段：**匹配证据 / 缺失项 / 风险点 / 面试问题**。
