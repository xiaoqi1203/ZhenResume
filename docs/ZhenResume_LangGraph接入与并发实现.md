# ZhenResume 全代码路线：LangGraph 接入与 Agent 并发实现

> ✅ **实施状态（已落地并验证）**：Step 0–5 已实现，Step 6 的僵尸回收已实现（尚未做"跑一半 kill 进程"的端到端演练）。
> 实测结果：
> - `WORKFLOW_ENGINE=langgraph`（串行）→ **`scripts/verify` 174/174 通过**；
> - `WORKFLOW_ENGINE=langgraph` + `WORKFLOW_PARALLEL=true` + `LANGGRAPH_CHECKPOINT=sqlite` → **174/174 通过**；
> - `WORKFLOW_ENGINE=legacy` → **174/174 通过**（回归未破）；
> - **两引擎逐字段比对**：7 份报告的五维分、`risk_level`、`summary`、发现项（含引文与排序）**完全一致**；
>   唯一差异是 `knowledge_refs[].doc_id/chunk_id` —— 那是每次重新灌种子知识库时新生成的 UUID，与引擎无关；
> - **重放幂等**：同一 `thread_id` 重复执行，`workflow_steps` 仍为 8 行、报告不重复、发现项数不变。
>
> 落地文件：`workflow/state.py`、`steps.py`、`nodes.py`、`persist.py`、`graph.py`、`common.py`；
> 开关：`WORKFLOW_ENGINE` / `WORKFLOW_PARALLEL` / `LANGGRAPH_CHECKPOINT`（见 `.env.example`）。
> 依赖：`langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2`（均 MIT；**不装** `langgraph-cli`/Agent Server）。

> 决策文档 · v1.0 · 取代 [ZhenResume_Dify-LangGraph混合架构方案.md](ZhenResume_Dify-LangGraph混合架构方案.md) 里的「能力层 = Dify」部分。
> 前置基线：`python -m scripts.verify` **174/174 全绿**（实测，未配置 LibreOffice 口径）。改造后这份基线必须仍然全绿。
> 本文原则：**零契约变更、零前端改动、`agents/` 下 8 个文件一行不改。**

---

## 0. 决策：全代码，去掉 Dify

### 0.1 去掉 Dify 之后，架构反而更贴近项目原始设计

`aaa.md` §四 的原始技术栈写的是：**LangGraph + PostgreSQL/pgvector + Langfuse**。Dify 从不在原始设计里。所以"全代码"不是退让，而是**回到文档设计**。

| 层 | 组件 | 状态 |
|---|---|---|
| 契约/业务层 | FastAPI + SQLAlchemy（SQLite → PostgreSQL） | 保留，零改动 |
| 编排层 | **LangGraph 1.2.x**（MIT，只用 OSS 库） | 新增，替换 `workflow/engine.py` |
| 检索层 | 本地混合检索（现状）→ **真实 embedding + pgvector + 可选 rerank** | 改造 `services/rag.py` / `embeddings.py` |
| Prompt | `services/prompts.py` + `prompt_templates` 表（已有版本自增与 API） | 保留，零改动 |
| 可观测 | `workflow_steps` 表（已有 tokens/cost/latency）＋可选 Langfuse | 保留，可选增强 |
| 运营型应用 | 直接用代码写端点 | 不需要平台 |

### 0.2 去掉 Dify 一次性消掉的问题（这是收益，不是损失）

| 原 Dify 方案里的问题 | 全代码后的状态 |
|---|---|
| 十多个容器、约 8 GB 常驻内存，与前后端抢资源 | **零额外容器** |
| 简历原文可能落到第三方平台的运行日志 | **数据不出自家库** |
| 多租户 SaaS 授权条款、升级破坏向量检索、SSRF 私网白名单、日志不清理 | **全部消失** |
| 知识库双写与同步一致性问题（`knowledge_sync_state`） | **不需要**：知识库真源本来就在 `knowledge_docs/chunks` |
| Prompt 真源与 Dify 之间的"试验场—回流"流程 | **不需要**：`prompt_templates` 就是唯一真源 |
| Dify run 不幂等 / 无 async 模式 | **不存在** |
| 混合架构没有任何官方背书 | **不存在**（LangGraph 是普通 Python 库） |

### 0.3 但必须自己补回来的一件事：检索质量

Dify 唯一真正值钱的能力是"现成的真实向量 + rerank"。去掉它之后，`services/embeddings.py:1-12` 自述的短板还在（本地 blake2b 哈希向量，不是语义向量）。

**补法（不引入平台，只改两个文件 + 一份 DDL）：**

| 项 | 现状 | 目标 | 改动面 |
|---|---|---|---|
| embedding | 本地确定性哈希（1536 维） | 调用 **OpenAI 兼容的 embedding 接口**（如 bge-m3 / text-embedding-3-small，任何兼容网关都行） | `services/embeddings.py` 加 `provider=hash|http` 分支 |
| 向量存储 | `knowledge_chunks.embedding` 是 JSON 数组 + Python 侧全量点积 | **pgvector `Vector(1536)` + HNSW 索引** | DDL 一处 + `services/rag.py` 改成 SQL `ORDER BY embedding <=> :q` |
| 混合打分 | `0.6 × 向量 + 0.4 × 词面`（Python 侧） | SQL 里做向量候选，词面分仍用现有 `lexical_overlap` 复排 | `services/rag.py` |
| rerank（可选） | 无 | 调一个 rerank 接口（Cohere / BGE-reranker 等）对 top-20 复排到 top-5 | 新增 `services/rerank.py`（约 60 行） |
| 降级 | —— | 嵌入服务不可用时**回落哈希向量**，报告照出 | `embeddings.embed()` 内部 try/except |

> 关键约束：**哈希向量与语义向量的分数不可比**。切换 provider 时必须重建索引（`scripts/seed_knowledge.py --force`），并且 `verify.py` 的「检索结果按相关度降序」断言要在**同一 provider 下**比较。

---

## 1. 依赖与版本（必须钉死）

```
# backend/requirements.txt 追加
langgraph==1.2.15
langgraph-checkpoint-sqlite==3.1.2        # 开发/单机
langgraph-checkpoint-postgres==3.2.0      # 生产
psycopg[binary]==3.2.3                    # 仅 Postgres 路线需要
pgvector==0.3.6                           # 仅 pgvector 路线需要
```

已核实的限制（决定了怎么写代码）：

| 事实 | 影响 |
|---|---|
| `langgraph` 1.2.x 是 **MIT**，`requires_python>=3.10` | 本项目 venv 是 **3.10.8**，可用 |
| `langgraph-api`（Agent Server）是 **Elastic-2.0**，生产需 license key + 外联 beacon | **不用**；只 `pip install` 库，自己嵌 FastAPI |
| Postgres checkpointer 依赖 **psycopg 3**（不是 asyncpg） | DSN 要写成 `postgresql://`（去掉 SQLAlchemy 的 `+psycopg`） |
| Python < 3.11 + async：必须**显式**把 `config` 传进 `ainvoke`；`get_stream_writer()` 不可用 | 代码里一律显式传 `config=`；不使用 stream writer |
| Windows + 中文 locale：`langgraph dev` 会崩（GBK），官方 CI 无 Windows runner | **不用 `langgraph-cli`**；启动脚本设 `PYTHONUTF8=1` |
| 同一 superstep 内两个节点写同一个 channel（无 reducer）会抛 `InvalidUpdateError` | **并行的前提**：见 §3.2 与 §4.5，必须按节点声明字段 |
| checkpoint 无内置 TTL，需要自己清理 | 新增 `scripts/prune_checkpoints.py` + 定时任务 |

---

## 2. 接入 LangGraph：8 步

> 每一步结尾都有"验收"，必须过了才进下一步。全程保留 `WORKFLOW_ENGINE=legacy` 开关，随时可回退。

### Step 0：开关与唯一索引（不动逻辑，先铺路）

**`app/config.py`** 追加：

```python
    # ---- 工作流引擎 ----
    workflow_engine: str = "legacy"            # legacy | langgraph
    workflow_parallel: bool = False            # ats/impact/risk 并行
    workflow_rework_max: int = 0               # 质检回环次数，0=关闭
    langgraph_checkpoint: str = "off"          # off | sqlite | postgres
    langgraph_checkpoint_url: str = ""         # 留空则按 database_url 推导
    zombie_task_timeout_s: int = 300           # 僵尸任务回收阈值
    llm_max_concurrency: int = 4               # 并发 LLM 调用上限
    hr_batch_concurrency: int = 3              # HR 批量并发上限
```

**`app/models.py`** 的 `WorkflowStep` 加唯一约束（**加不改**，不碰任何现有列）：

```python
from sqlalchemy import UniqueConstraint

class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "step_name", name="uq_workflow_step_task_name"),
    )
```

**`app/db.py`** 给 SQLite 加 busy timeout（并发写的必需项）：

```python
connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
```

> 为什么必须加：WAL 模式下仍然只有**一个写者**。并发节点同时 upsert 步骤行时，默认 5 秒会抛 `database is locked`；30 秒足够覆盖任何单条写入。

**验收**：`python -m scripts.verify` 仍然 174/174（唯一索引对现有行为无影响，因为现状就是每任务每步一行）。

---

### Step 1：`app/workflow/state.py`（状态契约）

```python
"""LangGraph 工作流状态与 AgentContext 适配。

三条铁律（来自 8 个 Agent 的实现事实，见方案 §1.2）：
1. findings 是「整体覆盖」语义（jd_match / suggestion / qc 三处都是 ctx.findings = [...]），
   **不加 reducer**。只有并行节点各自追加的字段才加 reducer。
2. 同一 superstep 内多个节点不得写同一个无 reducer 的 channel（LangGraph 会抛
   InvalidUpdateError）→ 每个节点只声明自己负责的字段（NODE_STATE_FIELDS）。
3. state 必须可 JSON 序列化：Session / httpx client / LLMClient 一律放 GraphContext。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any

from typing_extensions import TypedDict

from ..agents.base import AgentContext
from ..schemas import ParsedJd, ParsedResume

# 契约 §4.2 的 8 个 step_name，顺序即契约
CONTRACT_STEPS: tuple[str, ...] = (
    "parse", "jd_match", "ats", "impact", "risk", "suggestion", "report", "qc",
)

# 并行扇出的三个节点，以及归并时的固定顺序
PARALLEL_NODES: tuple[str, ...] = ("ats", "impact", "risk")
MERGE_ORDER: dict[str, int] = {name: i for i, name in enumerate(PARALLEL_NODES)}

# 每个节点允许写回 state 的字段（唯一真源，可被单测断言）
NODE_STATE_FIELDS: dict[str, tuple[str, ...]] = {
    "parse": ("parsed_resume", "parsed_jd", "role_family"),
    "jd_match": ("findings", "missing_keywords", "covered_keywords"),
    # ats/impact/risk 只做 append：串行版由 make_agent_node 额外补一条"整体写入 findings"
    # （与现状 engine 循环完全等价）；并行版只写 finding_batches，绝不写 findings。
    "ats": (),
    "impact": (),
    "risk": (),
    "suggestion": ("findings", "action_items"),
    "report": ("scores", "knowledge_refs", "summary"),
    "qc": ("findings", "qc_report"),
}


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """outputs 等字典字段的 reducer：按 key 合并，后写覆盖同 key。"""
    return {**(left or {}), **(right or {})}


class StepRecord(TypedDict, total=False):
    step_name: str
    status: str                 # running | done | degraded | failed
    tokens: int
    cost: float
    latency_ms: int
    notes: str | None
    input_digest: dict
    output_digest: dict
    contract_step: bool


class WorkflowState(TypedDict, total=False):
    # --- 输入（setup 节点装入）---
    task_id: str
    resume_text: str
    jd_text: str
    parsed_resume: dict
    parsed_jd: dict
    prompt_overrides: dict

    # --- 检索（retrieve 节点写入一次，全链路复用）---
    knowledge_hits: list

    # --- 累积字段（覆盖语义，不加 reducer）---
    findings: list
    missing_keywords: list
    covered_keywords: list
    action_items: list
    knowledge_refs: list
    scores: dict
    summary: str
    qc_report: dict
    role_family: str

    # --- 需要 reducer 的字段 ---
    outputs: Annotated[dict, merge_dict]        # 并行节点各写自己那一个 key
    finding_batches: Annotated[list, operator.add]   # 并行节点各 append 一个批次
    steps: Annotated[list, operator.add]        # 审计/用量回流

    # --- 控制与产出 ---
    report_id: str | None
    rework_count: int
    review_decision: str | None


@dataclass
class GraphContext:
    """不进 checkpoint 的运行时依赖（LangGraph 1.x 的 context API）。"""

    llm: Any = None
    retriever: Any = None
    semaphore: Any = None       # asyncio.Semaphore，限制并发 LLM 调用


def safe_resume(raw: dict | None) -> ParsedResume:
    try:
        return ParsedResume.model_validate(raw or {})
    except Exception:                                   # noqa: BLE001
        return ParsedResume.empty()


def safe_jd(raw: dict | None) -> ParsedJd:
    try:
        return ParsedJd.model_validate(raw or {})
    except Exception:                                   # noqa: BLE001
        return ParsedJd.empty()


def ctx_from_state(state: WorkflowState) -> AgentContext:
    """state → AgentContext。与现状 engine.py:65-72 的构造完全一致。"""
    ctx = AgentContext(
        task_id=state["task_id"],
        resume_text=state.get("resume_text", ""),
        jd_text=state.get("jd_text", ""),
        parsed_resume=safe_resume(state.get("parsed_resume")),
        parsed_jd=safe_jd(state.get("parsed_jd")),
        role_family=state.get("role_family", "general"),
        knowledge_hits=list(state.get("knowledge_hits") or []),
        prompt_overrides=dict(state.get("prompt_overrides") or {}),
    )
    ctx.findings = list(state.get("findings") or [])
    ctx.missing_keywords = list(state.get("missing_keywords") or [])
    ctx.covered_keywords = list(state.get("covered_keywords") or [])
    ctx.action_items = list(state.get("action_items") or [])
    ctx.knowledge_refs = list(state.get("knowledge_refs") or [])
    ctx.scores = dict(state.get("scores") or {})
    ctx.summary = state.get("summary", "")
    ctx.qc_report = dict(state.get("qc_report") or {})
    ctx.outputs = dict(state.get("outputs") or {})
    return ctx


def state_slice(agent_name: str, ctx: AgentContext) -> dict:
    """按 NODE_STATE_FIELDS 取该节点负责的字段。**这是并行安全的关键**。"""
    out: dict = {}
    for field_name in NODE_STATE_FIELDS.get(agent_name, ()):
        value = getattr(ctx, field_name)
        if field_name in ("parsed_resume", "parsed_jd"):
            value = value.model_dump()
        out[field_name] = value
    return out


def delta_findings(before: list, after: list) -> list:
    """取节点新增的发现项。

    ats / impact / risk 只做 ctx.add_findings()（append），所以前缀不变；
    万一将来有人改成替换，这里退化为"按内容差集"，不会静默丢条目。
    """
    if len(after) >= len(before) and after[: len(before)] == before:
        return list(after[len(before):])
    seen = {repr(item) for item in before}
    return [item for item in after if repr(item) not in seen]
```

**验收**：`python -c "from app.workflow.state import CONTRACT_STEPS, NODE_STATE_FIELDS; assert len(CONTRACT_STEPS)==8"`。

---

### Step 2：`app/workflow/steps.py`（幂等落库 + 进度）

```python
"""workflow_steps 的幂等写入与 progress 推进。

为什么必须幂等：LangGraph 的 replay / resume 会**真的重跑节点**，
插入式写法会产生第二行，'步骤数为 8' 与成本核算立刻失真。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import session_scope
from ..models import AnalysisTask, WorkflowStep
from .state import CONTRACT_STEPS

# 每个任务一把锁：并发节点写库时串行化，避免 SQLite/连接池竞争
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(task_id: str) -> asyncio.Lock:
    lock = _locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[task_id] = lock
    return lock


async def record_step(task_id: str, step_name: str, **fields) -> None:
    """按 (task_id, step_name) upsert 一行，并推进 progress。"""
    async with _lock_for(task_id):
        # 阻塞式 DB 调用放到线程里，避免卡住事件循环（并发场景必需）
        await asyncio.to_thread(_record_step_sync, task_id, step_name, fields)


def _record_step_sync(task_id: str, step_name: str, fields: dict) -> None:
    with session_scope() as db:
        row = db.execute(
            select(WorkflowStep).where(
                WorkflowStep.task_id == task_id, WorkflowStep.step_name == step_name
            )
        ).scalar_one_or_none()
        if row is None:
            row = WorkflowStep(task_id=task_id, step_name=step_name, status="running")
            db.add(row)
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)
        db.flush()
        _advance_progress(db, task_id)


def _advance_progress(db, task_id: str) -> None:
    """progress = 已完成的契约步（去重） / 8。重放安全，最终必为 1.0。"""
    rows = db.execute(select(WorkflowStep).where(WorkflowStep.task_id == task_id)).scalars().all()
    done = {r.step_name for r in rows if r.status in ("done", "degraded", "failed")}
    ordered = [name for name in CONTRACT_STEPS if name in done]
    task = db.get(AnalysisTask, task_id)
    if task is None:
        return
    task.progress = round(len(ordered) / len(CONTRACT_STEPS), 4)
    task.status = "running"
    if ordered:
        task.current_step = ordered[-1]


def fail_task(task_id: str, message: str) -> None:
    """任务级失败（critical 节点异常、图异常终止）。"""
    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return
        task.status = "failed"
        task.error = message[:500]
        task.completed_at = datetime.now(timezone.utc)
```

**验收**：单测「同一 `(task_id, step_name)` 连写两次 → 表里只有 1 行」。

---

### Step 3：`app/workflow/nodes.py`（把 8 个 Agent 包成节点）

```python
"""图节点。**agents/ 下 8 个文件一行不改**，这里只做适配。"""

from __future__ import annotations

import time
from collections.abc import Callable

from langgraph.runtime import Runtime

from ..agents import AGENT_PIPELINE
from ..agents.base import AgentOutput
from ..config import settings
from ..db import session_scope
from ..models import AnalysisTask, JobDescription, Resume
from ..services import prompts, rag
from .state import (
    GraphContext, WorkflowState, ctx_from_state, delta_findings, state_slice,
)
from .steps import record_step


class WorkflowAbort(RuntimeError):
    """critical 节点失败 → 整条任务失败（与现状 engine.py:101-106 语义一致）。"""


# --------------------------------------------------------------------------- #
# 非契约节点（不写 workflow_steps，不计入 progress）
# --------------------------------------------------------------------------- #
def setup_node(state: WorkflowState) -> dict:
    """取业务数据 + prompt 覆盖项。只读，无副作用。"""
    with session_scope() as db:
        task = db.get(AnalysisTask, state["task_id"])
        if task is None:
            raise WorkflowAbort("任务不存在")
        resume = db.get(Resume, task.resume_id) if task.resume_id else None
        jd = db.get(JobDescription, task.jd_id) if task.jd_id else None
        if resume is None or jd is None:
            raise WorkflowAbort("简历或 JD 记录缺失")
        return {
            "resume_text": resume.raw_text or "",
            "jd_text": jd.raw_text or "",
            "parsed_resume": resume.parsed_json or {},
            "parsed_jd": jd.parsed_json or {},
            "prompt_overrides": prompts.load_overrides(db),
            "rework_count": 0,
            "report_id": None,
        }


def retrieve_node(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
    """一次检索、全链路复用（保住现状 engine.py:74-77 的 F3 决策）。"""
    query = rag.build_query(state.get("parsed_jd") or {}, state.get("parsed_resume") or {})
    if not query.strip():
        return {"knowledge_hits": []}
    with session_scope() as db:
        retriever = runtime.context.retriever or rag.LocalHybridRetriever()
        return {"knowledge_hits": retriever.search(db, query, top_k=settings.rag_top_k)}


def merge_node(state: WorkflowState) -> dict:
    """并行扇出的确定性归并：固定顺序 ats → impact → risk。

    不固定顺序的后果：findings 顺序随调度抖动，
    verify.py 的「发现项按 red→yellow→green 排序」在平级时不再可复现。
    """
    batches = sorted(
        state.get("finding_batches") or [],
        key=lambda b: _MERGE_ORDER.get(b.get("node"), 99),
    )
    merged = list(state.get("findings") or [])
    for batch in batches:
        merged.extend(batch.get("items") or [])
    return {"findings": merged}


_MERGE_ORDER = {"ats": 0, "impact": 1, "risk": 2}


# --------------------------------------------------------------------------- #
# 8 个契约节点（工厂）
# --------------------------------------------------------------------------- #
def make_agent_node(agent_cls: type, *, parallel_safe: bool = False) -> Callable:
    """把一个现有 Agent 包成图节点。

    parallel_safe=True 时（ats/impact/risk 在并行版），
    发现项不写 findings 而是写 finding_batches —— 否则同一 superstep 内
    三个节点写同一个 channel，LangGraph 直接抛 InvalidUpdateError。
    """

    async def node(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
        agent = agent_cls()
        task_id = state["task_id"]
        ctx = ctx_from_state(state)
        before = list(ctx.findings)

        await record_step(task_id, agent.name, status="running")

        started = time.perf_counter()
        semaphore = runtime.context.semaphore
        try:
            if semaphore is not None:
                async with semaphore:                       # 并发限流（§4.5 坑 4）
                    out: AgentOutput = await agent.safe_run(ctx, runtime.context.llm)
            else:
                out = await agent.safe_run(ctx, runtime.context.llm)
        except Exception as exc:                            # 只有 critical（parse）会到这里
            await record_step(task_id, agent.name, status="failed",
                              notes=f"{type(exc).__name__}: {str(exc)[:300]}")
            raise WorkflowAbort(f"{agent.title} 执行失败：{str(exc)[:200]}") from exc

        latency = out.latency_ms or int((time.perf_counter() - started) * 1000)
        status = "degraded" if out.status == "degraded" else "done"
        await record_step(task_id, agent.name, status=status, tokens=out.tokens,
                          cost=out.cost, latency_ms=latency, notes=out.notes)

        patch: dict = state_slice(agent.name, ctx)
        if not parallel_safe and agent.name in _MERGE_ORDER:
            # 串行版：与现状完全等价的「整体写入」语义
            patch["findings"] = ctx.findings
        # 每个节点只写自己那一个 output key（outputs 用 merge_dict reducer）
        patch["outputs"] = {agent.name: out.data}
        patch["steps"] = [{
            "step_name": agent.name, "contract_step": True, "status": status,
            "tokens": out.tokens, "cost": out.cost, "latency_ms": latency,
            "notes": out.notes,
        }]
        if parallel_safe and agent.name in _MERGE_ORDER:
            patch.pop("findings", None)
            patch["finding_batches"] = [
                {"node": agent.name, "items": delta_findings(before, ctx.findings)}
            ]
        return patch

    node.__name__ = f"node_{agent_cls.name}"
    return node


def build_agent_nodes() -> dict[str, Callable]:
    """node 名必须逐字等于 AGENT_PIPELINE[].name（契约步名）。"""
    return {cls.name: make_agent_node(cls) for cls in AGENT_PIPELINE}


def parallel_agent_nodes() -> dict[str, Callable]:
    return {name: make_agent_node(cls, parallel_safe=True)
            for name, cls in ((c.name, c) for c in AGENT_PIPELINE)
            if name in _MERGE_ORDER}
```

**验收**：`python -c "from app.workflow.nodes import build_agent_nodes; n=build_agent_nodes(); assert list(n)==['parse','jd_match','ats','impact','risk','suggestion','report','qc']"`。

---

### Step 4：`app/workflow/graph.py`（图 + checkpoint）

```python
"""LangGraph 图。串行版与并行版共用一个 builder。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from ..config import settings
from ..services.llm import get_llm
from ..services import rag
from .nodes import (
    WorkflowAbort, build_agent_nodes, merge_node, parallel_agent_nodes,
    retrieve_node, setup_node,
)
from .state import CONTRACT_STEPS, GraphContext, WorkflowState
from .steps import fail_task

_SERIAL_TAIL = ("ats", "impact", "risk", "suggestion", "report", "qc")


def route_after_qc(state: WorkflowState) -> str:
    """质检后的条件边：回环 or 定稿。

    默认 WORKFLOW_REWORK_MAX=0 → 永远走 finalize，行为与现状完全一致。
    """
    if settings.workflow_rework_max <= 0:
        return "finalize"
    dropped = int((state.get("qc_report") or {}).get("dropped") or 0)
    evaluated = max(int((state.get("qc_report") or {}).get("evaluated") or 1), 1)
    if dropped / evaluated > 0.5 and int(state.get("rework_count") or 0) < settings.workflow_rework_max:
        return "rework"
    return "finalize"


def build_graph(*, parallel: bool | None = None, checkpointer=None):
    parallel = settings.workflow_parallel if parallel is None else parallel
    g = StateGraph(WorkflowState, context_schema=GraphContext)

    g.add_node("setup", setup_node)
    g.add_node("retrieve", retrieve_node,
               retry_policy=RetryPolicy(max_attempts=3))       # 只保护检索边界
    nodes = build_agent_nodes()
    for name in ("parse", "jd_match", "suggestion", "report", "qc"):
        g.add_node(name, nodes[name])
    if parallel:
        for name, fn in parallel_agent_nodes().items():
            g.add_node(name, fn)
        g.add_node("merge", merge_node)
    else:
        for name in ("ats", "impact", "risk"):
            g.add_node(name, nodes[name])
    g.add_node("finalize", finalize_node)
    g.add_node("persist", persist_node)
    if settings.workflow_rework_max > 0:
        g.add_node("rework", rework_node)

    g.add_edge(START, "setup")
    g.add_edge("setup", "retrieve")
    g.add_edge("retrieve", "parse")
    g.add_edge("parse", "jd_match")

    if parallel:
        for name in ("ats", "impact", "risk"):
            g.add_edge("jd_match", name)          # 扇出
            g.add_edge(name, "merge")             # 汇聚（等齐三个）
        g.add_edge("merge", "suggestion")
    else:
        g.add_edge("jd_match", "ats")
        g.add_edge("ats", "impact")
        g.add_edge("impact", "risk")
        g.add_edge("risk", "suggestion")

    g.add_edge("suggestion", "report")
    g.add_edge("report", "qc")
    # 注意：qc 只能用条件边，不能同时挂静态边（官方明确警告两者会同时执行）
    g.add_conditional_edges("qc", route_after_qc,
                            {"finalize": "finalize", "rework": "rework"})
    if settings.workflow_rework_max > 0:
        g.add_edge("rework", "suggestion")
    g.add_edge("finalize", "persist")
    g.add_edge("persist", END)

    graph = g.compile(checkpointer=checkpointer)

    # 把"图节点集合"与"契约步集合"的关系固化，防止有人把非契约节点写进表
    contract_in_graph = {n for n in graph.get_graph().nodes if n in CONTRACT_STEPS}
    assert contract_in_graph == set(CONTRACT_STEPS), contract_in_graph
    assert len(CONTRACT_STEPS) == 8
    return graph


# --------------------------------------------------------------------------- #
# finalize（= 现状 engine.py:116-120 的"qc 之后重算分数"）
# --------------------------------------------------------------------------- #
def finalize_node(state: WorkflowState) -> dict:
    from ..services import analyze
    from .state import ctx_from_state

    ctx = ctx_from_state(state)
    ctx.scores = analyze.compute_scores(
        ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text
    )
    return {"scores": ctx.scores}


def rework_node(state: WorkflowState) -> dict:
    return {"rework_count": int(state.get("rework_count") or 0) + 1}


async def persist_node(state: WorkflowState) -> dict:
    report_id = await asyncio.to_thread(_persist_sync, state)
    return {"report_id": report_id}


# --------------------------------------------------------------------------- #
# checkpointer：按任务构建（避开"全局 async saver + asyncio.run"的事件循环绑定）
# --------------------------------------------------------------------------- #
def _to_psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def make_checkpointer():
    mode = settings.langgraph_checkpoint
    if mode == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from ..config import DATA_DIR

        path = settings.langgraph_checkpoint_url or str(DATA_DIR / "zhenresume_checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            await saver.setup()
            yield saver
    elif mode == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        dsn = settings.langgraph_checkpoint_url or _to_psycopg_dsn(settings.database_url)
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await saver.setup()          # 首次建表；幂等，可每次调用
            yield saver
    else:
        yield None


async def run_graph(task_id: str) -> str | None:
    """后台任务入口（供 launch_workflow 调用）。"""
    async with make_checkpointer() as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        context = GraphContext(
            llm=get_llm(),
            retriever=rag.get_retriever(),
            semaphore=asyncio.Semaphore(max(1, settings.llm_max_concurrency)),
        )
        try:
            final = await graph.ainvoke(
                {"task_id": task_id},
                # Python 3.10 + async：必须显式传 config（官方已知限制）
                config={"configurable": {"thread_id": task_id}},
                context=context,
                durability="async",      # 崩溃可续跑；要求更高就用 "sync"
            )
        except WorkflowAbort as exc:
            fail_task(task_id, str(exc))
            return None
        except Exception as exc:         # noqa: BLE001
            fail_task(task_id, f"工作流异常终止：{type(exc).__name__}: {str(exc)[:200]}")
            raise
    return final.get("report_id")
```

`_persist_sync`（放在 `workflow/persist.py`，幂等）：**按 `task_id` upsert 报告**，发现项**先删后插**，任务置 `done/progress=1.0/current_step="qc"`。

```python
def _persist_sync(state: WorkflowState) -> str:
    from datetime import datetime, timezone
    from ..agents.report_agent import DISCLAIMER
    from ..models import AnalysisReport, ReportFinding
    from ..services import analyze
    from .state import ctx_from_state

    ctx = ctx_from_state(state)
    task_id = state["task_id"]
    with session_scope() as db:
        report = db.execute(
            select(AnalysisReport).where(AnalysisReport.task_id == task_id)
        ).scalar_one_or_none()
        if report is None:
            report = AnalysisReport(task_id=task_id)
            db.add(report)
            db.flush()
        db.query(ReportFinding).filter(ReportFinding.report_id == report.id).delete(
            synchronize_session=False
        )
        scores = ctx.scores
        report.overall_score = scores.get("overall")
        report.ats_score = scores.get("ats")
        report.impact_score = scores.get("impact")
        report.clarity_score = scores.get("clarity")
        report.match_score = scores.get("match")
        report.risk_score = scores.get("risk")
        report.risk_level = analyze.risk_level_from_score(int(scores.get("risk") or 0))
        report.summary = ctx.summary
        report.extra_json = {
            "missing_keywords": ctx.missing_keywords[:20],
            "covered_keywords": ctx.covered_keywords[:30],
            "action_items": ctx.action_items,
            "knowledge_refs": ctx.knowledge_refs,
            "qc": ctx.qc_report,
            "disclaimer": DISCLAIMER,
            "agent_outputs": {k: v for k, v in ctx.outputs.items() if k != "parse"},
        }
        for order, finding in enumerate(analyze.sort_findings(ctx.findings)):
            db.add(ReportFinding(
                report_id=report.id,
                category=finding.get("category"),
                severity=finding.get("severity"),
                title=(finding.get("title") or "")[:250],
                original_text=finding.get("original_text"),
                issue=finding.get("issue"),
                suggestion=finding.get("suggestion"),
                rewritten_text=finding.get("rewritten_text"),
                evidence_ref=finding.get("evidence"),
                sort_order=order,
            ))
        task = db.get(AnalysisTask, task_id)
        if task is not None:
            task.status = "done"
            task.progress = 1.0
            task.current_step = "qc"
            task.completed_at = datetime.now(timezone.utc)
        db.flush()
        return report.id
```

> `extra_json` 的键、`ReportFinding` 的字段顺序与 `engine._persist_report` 逐一对应 —— 这是 `serializers.serialize_report` 与前端报告页的依赖，不能改。

**验收**：`WORKFLOW_ENGINE=langgraph` 下 `python -m scripts.verify` **174/174**。

---

### Step 5：`app/workflow/engine.py`（最小 diff 的开关）

```python
def launch_workflow(task_id: str) -> None:
    """后台任务入口。签名与调用方（api/tasks.py、api/hr.py）完全不变。"""
    try:
        if settings.workflow_engine == "langgraph":
            from .graph import run_graph

            asyncio.run(run_graph(task_id))
        else:
            asyncio.run(run_workflow(task_id))       # 现状实现，原样保留
    except Exception:                                 # noqa: BLE001
        traceback.print_exc()
        from .steps import fail_task

        fail_task(task_id, "工作流异常终止")           # 兜底：不留 running 僵尸
```

`_safe_resume` / `_safe_jd` / `_persist_report` / `_input_digest` / `_truncate` 原样保留（legacy 继续用）。

**验收**：`WORKFLOW_ENGINE` 两种取值下各跑一遍 `verify.py`，都是 174/174；同一份简历在两种引擎下**分数完全相同**。

---

### Step 6：`main.py` lifespan（僵尸回收 + 清理）

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    with session_scope() as db:
        seed_if_empty(db)
    if settings.workflow_engine == "langgraph" and settings.langgraph_checkpoint != "off":
        asyncio.create_task(_resume_zombies())
    yield
```

```python
async def _resume_zombies() -> None:
    """服务重启后，把卡在 running 且心跳超时的任务从断点续跑。

    心跳 = 该任务最近一行 workflow_steps 的 created_at（用现有列，无需加字段）。
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, select
    from .models import AnalysisTask, WorkflowStep
    from .workflow.graph import run_graph

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.zombie_task_timeout_s)
    with session_scope() as db:
        last_step = (
            select(WorkflowStep.task_id, func.max(WorkflowStep.created_at).label("hb"))
            .group_by(WorkflowStep.task_id)
            .subquery()
        )
        rows = db.execute(
            select(AnalysisTask.id)
            .outerjoin(last_step, last_step.c.task_id == AnalysisTask.id)
            .where(AnalysisTask.status == "running",
                   func.coalesce(last_step.c.hb, AnalysisTask.created_at) < cutoff)
        ).scalars().all()
    for task_id in rows:
        await run_graph(task_id)          # 同一 thread_id → 已完成节点不会重跑
```

配套 `scripts/prune_checkpoints.py`（按 `created_at` 删旧 checkpoint，OSS 无内置 TTL），加进现有定时/启动流程。

**验收**：手动 `kill` 掉跑在 `impact` 的进程 → 重启 → 任务自愈到 `done`，`workflow_steps` 仍是 8 行、无重复；报告的 tokens/cost 没有翻倍。

---

### Step 7：灰度与回退

1. 本地 `WORKFLOW_ENGINE=langgraph` + `LANGGRAPH_CHECKPOINT=off` 跑一周；
2. 打开 `LANGGRAPH_CHECKPOINT=sqlite`，验证僵尸回收；
3. 线上（若有）按用户维度灰度：`X-Device-Id` 取模分流，两侧都走同一套 `services/*`，出错只回退开关；
4. 任何一步 `verify.py` 不绿 → 立刻 `WORKFLOW_ENGINE=legacy`。

---

## 3. Agent 并发：三种形态与选型

### 3.1 形态一：图内静态并行（**先做这个**）

场景：`ats` / `impact` / `risk` 三者互不依赖，都只往 `findings` 追加。串行下它们是 3 段 LLM 延迟，并行后约等于 1 段。

```
        ┌→ ats ────┐
jd_match├→ impact ──┼→ merge → suggestion → report → qc → finalize → persist
        └→ risk ────┘
```

实现要点（缺一不可）：

1. **三个节点只写 `finding_batches`**（`make_agent_node(cls, parallel_safe=True)`），**不写 `findings`**。同一 superstep 内两个节点写同一个无 reducer 的 channel，LangGraph 会抛 `InvalidUpdateError`。
2. `finding_batches` 用 `operator.add`，元素带 `{"node": "ats", "items": [...]}` 以便归并排序。
3. `outputs` 用 `merge_dict` reducer，每个节点只写 `{agent.name: out.data}`。
4. `merge` 节点按 **固定顺序 ats → impact → risk** 展平 —— 这是确定性的唯一保障。
5. `steps` 用 `operator.add`（三个节点各写一条审计记录）。

**收益**：live 模式单份省约 2 × 单节点耗时（每节点一次 LLM 调用、约 2–6 秒）。
**代价**：mock 模式零收益；`workflow_steps` 的 `latency_ms` 仍是各节点自身耗时（与现状一致）。

### 3.2 形态二：`Send` 动态扇出（map-reduce）—— 用在这里最有价值

上面是**固定 3 个**节点，用静态边就够。`Send` 解决的是"**数量运行时才知道**"的场景。本项目有一个非常合适的用例：**按经历条目并行做影响力分析**。

现状 `impact_agent` 是"整篇简历一次 LLM 调用"，导致：

- 长简历被 `_bullets_digest` 截断（`impact_agent.py:70-77`），后面的经历拿不到建议；
- 一条 bullet 的问题会稀释在整篇判断里。

改成 map-reduce：

```python
from langgraph.types import Send

def fan_out_bullets(state: WorkflowState) -> list[Send]:
    """每条要点一个子任务，上下文只有那一条 → 建议更准，且天然并发。"""
    ctx = ctx_from_state(state)
    bullets = ctx.parsed_resume.bullets()[: settings.impact_max_bullets]   # 上限 12
    return [Send("impact_one", {"bullet": b, "role_family": ctx.role_family,
                                "knowledge_hits": ctx.knowledge_hits,
                                "task_id": state["task_id"]}) for b in bullets]


def impact_one_node(state: dict) -> dict:
    """单条要点的影响力判断（复用 analyze 的规则 + 一次 LLM 调用）。"""
    ...
    return {"finding_batches": [{"node": "impact", "items": items}]}
```

```python
g.add_conditional_edges("jd_match", fan_out_bullets, ["impact_one"])
g.add_edge("impact_one", "merge")
```

注意三个坑：

- `Send` 只能从**条件边**返回；
- 子任务数量要设上限（`impact_max_bullets`），否则一份 40 条要点的简历会打 40 次 LLM；
- 每个子任务都必须能从**自己那份 state** 里拿到全部输入（LangGraph 会把 payload 覆盖进 state），不要依赖"兄弟节点写了什么"。

**收益**：这是**质量收益**，不是单纯提速 —— 逐条分析比整篇分析更准，且不受摘要截断影响。
**代价**：LLM 调用次数从 1 次变成 ≤12 次 → 必须配 `asyncio.Semaphore` 与成本上限校验（`verify.py` 有「单份成本 ≤ 0.5 元」断言）。

### 3.3 形态三：进程级 `asyncio` 并发（HR 批量）

`api/hr.py:53-72` 现在是**串行 for 循环**（代码注释自己写着"生产环境应换成 Redis + Celery 并发"）。每份简历是**完全独立的任务**，不需要 LangGraph 的 `Send`（它们不共享 state），只需要限流并发：

```python
async def _process_batch(batch_id: str) -> None:
    ...
    semaphore = asyncio.Semaphore(max(1, settings.hr_batch_concurrency))   # 默认 3

    async def one(item_id: str) -> None:
        async with semaphore:
            # 1) 建任务（独立事务）
            with session_scope() as db:
                ...
                task_id = task.id
            # 2) 跑工作流：每份简历一个独立 thread_id
            if settings.workflow_engine == "langgraph":
                from ..workflow.graph import run_graph
                report_id = await run_graph(task_id)
            else:
                report_id = await run_workflow(task_id)
            # 3) 回写批次条目（独立事务）
            with session_scope() as db:
                ...

    await asyncio.gather(*(one(i) for i in item_ids), return_exceptions=True)
```

关键点：

- 每个 `one()` 内部**不要**跨 `await` 持有 Session（现状是每段独立 `session_scope`，保持）；
- `return_exceptions=True`：单份失败不影响整批（现状是 try 包整批，语义更好）；
- `hr_batch_concurrency` 默认 **3**：100 份 × 约 20 秒 / 3 ≈ **11 分钟**；调到 5 约 **6.7 分钟**，满足 `aaa.md` 的「100 份 ≤ 10 分钟」；
- **HR 批量并发 > 1 时强烈建议切 PostgreSQL**：SQLite 单写者 + 3 个并发任务各写 8 行步骤 = 24 次写，靠 `timeout=30` 能扛，但吞吐会被写锁串起来。PG 是这一项的正解。

### 3.4 选型表

| 场景 | 用什么 | 为什么不用另一个 |
|---|---|---|
| ats / impact / risk 三路并行 | **静态边扇出 + merge**（形态一） | 节点数固定，`Send` 是过度设计 |
| 按要点/经历条目逐条分析 | **`Send` map-reduce**（形态二） | 静态边要预先知道条数 |
| HR 批量 N 份简历 | **`asyncio.Semaphore` + `gather`**（形态三） | 各份之间零共享状态，塞进一张图反而增加耦合（per-thread 子图还不支持并行工具调用） |
| 80% 的"加个并发"需求 | 形态一 | 先拿到收益，再考虑形态二 |

### 3.5 并发下的六个坑（都会真实咬人）

| # | 坑 | 现象 | 对策 |
|---|---|---|---|
| 1 | **同一 superstep 写同一 channel** | `InvalidUpdateError: Can receive only one value per step` | 按节点声明字段（`NODE_STATE_FIELDS`）；并行字段用 reducer（`finding_batches`、`steps`、`outputs`） |
| 2 | **SQLite 单写者** | `database is locked`；`progress` 更新丢失 | `connect_args={"timeout": 30}`；每次写库用 `asyncio.Lock` 串行化；并发 > 1 的批量任务切 PG |
| 3 | **阻塞式 DB 调用卡住事件循环** | 并发被"假并行"（其实是串行等待） | 所有库操作走 `asyncio.to_thread`（`record_step` 已如此） |
| 4 | **LLM 限流 / 成本失控** | 429；单份成本超 0.5 元 | `GraphContext.semaphore`（默认 4）+ 每节点仍写 tokens/cost 供看板核算 |
| 5 | **发现项顺序抖动** | 同一简历两次跑的 findings 顺序不同 | `merge` 固定顺序；`analyze.sort_findings` 之后仍按 severity 排序 |
| 6 | **重放导致重复计费** | 崩溃恢复后同一节点又打一次 LLM | 节点本身只调用 LLM（无业务副作用）；步骤写库幂等。**接受"极少数情况下重复调用 LLM"**，这是 checkpoint 换可用性的必然代价 |

> 补充：`services/llm.py::_call_deepseek` 目前**每次调用都新建 `httpx.AsyncClient`**（无连接池复用）。并发 3–12 路时 TLS 握手开销会明显。建议改成按事件循环缓存的共享 client（`asyncio.run` 场景要 per-loop 创建，不能模块级全局单例），预计降低单节点延迟 50–150 ms。

---

## 4. 原先 Dify 提供的价值，全代码下怎么补

| Dify 的能力 | 全代码替代 | 工作量 |
|---|---|---|
| 知识库文档 CRUD | **已有**：`api/knowledge.py` + `knowledge_docs/chunks` + `POST /knowledge/docs` | 0 |
| 真实语义向量 | `embeddings.py` 加 HTTP provider（OpenAI 兼容 / Ollama） | 0.5 天 |
| 向量库与近邻检索 | pgvector + HNSW（`ORDER BY embedding <=> :q`） | 1 天 |
| rerank | 新增 `services/rerank.py`：调一个 rerank 接口对 top-20 复排 | 0.5 天 |
| Prompt 版本管理 | **已有**：`prompt_templates` 表 + `GET/POST /admin/prompts`（版本自增） | 0 |
| 切片调试 / 命中测试 | **已有**：`POST /knowledge/search` + `scripts/inspect.py`；可加 `POST /admin/knowledge/preview` | 0.5 天（可选） |
| LLM 调用日志 | **已有**：`workflow_steps`（tokens/cost/latency/notes） | 0 |
| 分布式追踪 | 可选接 Langfuse（LangChain callback 一条线） | 0.5 天（可选） |
| 可视化编排 | **放弃**（全代码路线的主动取舍） | —— |
| 运营型 LLM 应用 | 直接写 FastAPI 端点 + 复用 `services/llm.py` | 按需 |

---

## 5. 落地顺序与验收总表

| 阶段 | 内容 | 验收 | 回退 |
|---|---|---|---|
| **S0** | 补测试：`compute_scores` 金标、`locate_quote` 用例、`parse_rules` 基线、`rag.search` 排序快照 | `pytest` 全绿 + `verify.py` 174/174 | 无（纯新增） |
| **S1** | Step 0~2：开关、唯一索引、`db.py` timeout、`state.py`、`steps.py` | `verify.py` 仍 174/174；重复写步骤只有 1 行 | 删文件 |
| **S2** | Step 3~5：节点、串行图、engine 开关 | `WORKFLOW_ENGINE=langgraph` 下 174/174；两引擎分数逐位相同 | 开关回 legacy |
| **S3** | Step 6：checkpoint + 僵尸回收 + 清理脚本 | kill 进程后自愈；步骤表无重复；checkpoint 行数不单调增长 | `CHECKPOINT=off` |
| **S4** | 形态一并发（`WORKFLOW_PARALLEL=true`） | 174/174；发现项顺序可复现；live 模式延迟下降 | `PARALLEL=false` |
| **S5** | 检索质量：embedding provider + pgvector（+ rerank） | 20 条 query 的 top-5 人工命中率提升；`知识库引用非空` 仍通过 | `RAG_BACKEND=local` |
| **S6** | 形态二（按要点 Send）与形态三（HR 批量并发） | 成本仍 ≤0.5 元/份；100 份 ≤10 分钟 | 各自开关 |
| **S7** | 可选：Langfuse、人工复核（需先改契约文档） | 新端点有契约条目 | 关端点 |

**预计**：S0–S4 约 **12–18 人日**（拿到断点续跑 + 并发 + 零回归）；S5 约 **2 人日**；S6–S7 按需。相比 Dify 混合方案（24–36 人日 + 十多个容器），全代码路线**省掉约一半工作量与全部容器运维**。
