"""LangGraph 图节点。**agents/ 下 8 个文件一行不改**，这里只做适配。

节点分两类（这条边界必须守住，否则契约 §4.2 会破）：
- **契约节点**：名字逐字等于 `AGENT_PIPELINE[].name`，写 `workflow_steps`、计入 progress；
- **非契约节点**：setup / retrieve / merge / finalize / persist，**不写 `workflow_steps`**。
"""

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
from .common import input_digest, truncate
from .state import (
    MERGE_ORDER,
    MERGE_SEQUENCE,
    GraphContext,
    WorkflowState,
    ctx_from_state,
    delta_findings,
    safe_jd,
    safe_resume,
    state_slice,
)
from .steps import record_step


class WorkflowAbort(RuntimeError):
    """critical 节点失败 → 整条任务失败（与现状 engine.py 的语义一致）。"""


# --------------------------------------------------------------------------- #
# 非契约节点
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
    """一次检索、全链路复用（保住现状"不每个 Agent 各检索一次"的决策）。"""
    query = rag.build_query(state.get("parsed_jd") or {}, state.get("parsed_resume") or {})
    if not query.strip():
        return {"knowledge_hits": []}
    with session_scope() as db:
        return {"knowledge_hits": rag.search(db, query, top_k=settings.rag_top_k)}


def merge_node(state: WorkflowState) -> dict:
    """并行扇出的确定性归并：按 MERGE_SEQUENCE 的固定顺序展平。

    不固定顺序的后果：findings 顺序随调度抖动，
    `verify.py` 的「发现项按 red→yellow→green 排序」在平级时不再可复现。
    完整图扇出 ats/impact/risk，HR 图扇出 jd_match/risk —— 两者都用同一张顺序表。
    """
    batches = sorted(
        state.get("finding_batches") or [],
        key=lambda b: MERGE_SEQUENCE.get(b.get("node"), 99),
    )
    merged = list(state.get("findings") or [])
    for batch in batches:
        merged.extend(batch.get("items") or [])
    return {"findings": merged}


def finalize_node(state: WorkflowState) -> dict:
    """质检之后重新定稿分数。

    质检会剔除/降级发现项，若不重算，报告总分与展示的问题清单会自相矛盾；但**不能**用
    `ctx.findings`（它含 LLM 每次增删的条目）—— 那会让同一份简历两次诊断分数不同。
    因此用 `compute_scores_deterministic`：分数只由规则层基线决定。
    """
    from ..services import analyze

    ctx = ctx_from_state(state)
    return {
        "scores": analyze.compute_scores_deterministic(
            ctx.parsed_resume, ctx.parsed_jd, ctx.role_family, ctx.resume_text, ctx.jd_text
        )
    }


# --------------------------------------------------------------------------- #
# 契约节点：把现有 Agent 包成节点
# --------------------------------------------------------------------------- #
def make_agent_node(agent_cls: type, *, parallel_safe: bool = False) -> Callable:
    """parallel_safe=True（并行版的 ats/impact/risk）时：

    发现项写 `finding_batches` 而不是 `findings` —— 否则同一 superstep 内
    三个节点写同一个无 reducer 的 channel，LangGraph 直接抛 `InvalidUpdateError`。
    """

    async def node(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
        agent = agent_cls()
        task_id = state["task_id"]
        ctx = ctx_from_state(state)
        before = list(ctx.findings)

        await record_step(task_id, agent.name, status="running", input_digest=input_digest(agent.name, ctx))

        started = time.perf_counter()
        semaphore = runtime.context.semaphore
        try:
            if semaphore is not None:
                async with semaphore:
                    out: AgentOutput = await agent.safe_run(ctx, runtime.context.llm)
            else:
                out = await agent.safe_run(ctx, runtime.context.llm)
        except Exception as exc:  # 只有 critical（parse）会走到这里
            await record_step(
                task_id, agent.name, status="failed",
                notes=f"{type(exc).__name__}: {str(exc)[:300]}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            raise WorkflowAbort(f"{agent.title} 执行失败：{str(exc)[:200]}") from exc

        latency = out.latency_ms or int((time.perf_counter() - started) * 1000)
        status = "degraded" if out.status == "degraded" else "done"
        await record_step(
            task_id, agent.name, status=status, tokens=out.tokens, cost=out.cost,
            latency_ms=latency, notes=out.notes, output_digest=truncate(out.data),
        )

        patch: dict = state_slice(agent.name, ctx)
        if parallel_safe:
            # 并行节点只写"自己新增的批次"，绝不整体写 findings ——
            # 同一 superstep 内多个节点写同一个无 reducer 的 channel，LangGraph 会抛
            # InvalidUpdateError。归并由 merge 节点按固定顺序完成。
            patch.pop("findings", None)
            patch["finding_batches"] = [
                {"node": agent.name, "items": delta_findings(before, ctx.findings)}
            ]
        elif agent.name in MERGE_ORDER:
            # 串行版：与现状 engine 循环完全等价的"整体写入"语义
            patch["findings"] = ctx.findings
        # 每个节点只写自己那一个 output key（outputs 用 merge_dict reducer）
        patch["outputs"] = {agent.name: out.data}
        patch["steps"] = [{
            "step_name": agent.name, "contract_step": True, "status": status,
            "tokens": out.tokens, "cost": out.cost, "latency_ms": latency, "notes": out.notes,
        }]
        return patch

    node.__name__ = f"node_{agent_cls.name}"
    return node


def build_agent_nodes() -> dict[str, Callable]:
    """node 名必须逐字等于 AGENT_PIPELINE[].name（契约步名）。"""
    return {cls.name: make_agent_node(cls) for cls in AGENT_PIPELINE}


def parallel_agent_nodes() -> dict[str, Callable]:
    return {
        cls.name: make_agent_node(cls, parallel_safe=True)
        for cls in AGENT_PIPELINE
        if cls.name in MERGE_ORDER
    }


# 让 safe_resume / safe_jd 在本模块可用（persist 与测试复用）
__all__ = [
    "WorkflowAbort",
    "build_agent_nodes",
    "finalize_node",
    "merge_node",
    "parallel_agent_nodes",
    "retrieve_node",
    "safe_jd",
    "safe_resume",
    "setup_node",
]


# --------------------------------------------------------------------------- #
# Agentic RAG 节点（非契约：不写 workflow_steps、不计 progress）
# --------------------------------------------------------------------------- #
async def agentic_evidence_node(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
    """Agent 自主多轮检索知识库，为**最终 findings**产出可校验的 chunk 级引用。

    放在 qc 之后的原因：
    - 只给"报告里真正会出现"的结论找依据，不浪费检索与 token；
    - Agent 不修改 findings（只挂 citations），因此不改变任何评分与结论；
    - 引用还要再过一次 `verify_citations`（chunk 存在 + quote 逐字可定位）。

    失败一律降级为"无引用"，绝不阻断报告产出。
    """
    if not settings.agentic_evidence_enabled:
        return {}

    findings = list(state.get("findings") or [])
    if not findings:
        return {"agent_trace": {"engine": "agentic", "skipped": "no_findings"}}

    # 只给模型精简字段，控制 token；index 与 sort_findings/persist 的顺序一致
    compact = [
        {
            "index": i,
            "title": f.get("title") or "",
            "issue": (f.get("issue") or "")[:120],
            "category": f.get("category"),
            "severity": f.get("severity"),
        }
        for i, f in enumerate(findings)
    ][:10]

    try:
        from ..agentic import AgentInputs, BaseAgent, get_spec
        from ..services.citation import attach_to_findings, citations_to_refs, verify_citations

        inputs = AgentInputs(
            resume_text=state.get("resume_text") or "",
            jd_text=state.get("jd_text") or "",
            parsed_resume=safe_resume(state.get("parsed_resume")),
            parsed_jd=safe_jd(state.get("parsed_jd")),
            role_family=state.get("role_family") or "general",
            extra={
                "missing_keywords": list(state.get("missing_keywords") or [])[:12],
                "findings": compact,
            },
        )
        run = await BaseAgent(get_spec(settings.agentic_evidence_agent)).run(inputs)

        raw_citations = (run.output or {}).get("citations") or []
        with session_scope() as db:
            verified, vstats = verify_citations(db, raw_citations)
        attached, astats = attach_to_findings(findings, verified)

        # 报告级 knowledge_refs 合并去重（固定检索的 top-3 + Agent 引用到的切片）
        refs = list(state.get("knowledge_refs") or [])
        seen = {(r.get("doc_id"), r.get("chunk_id")) for r in refs}
        for ref in citations_to_refs(verified):
            key = (ref.get("doc_id"), ref.get("chunk_id"))
            if key not in seen:
                seen.add(key)
                refs.append(ref)

        trace = {
            **run.trace,
            "status": run.status,
            "notes": run.notes,
            "reasoning": (run.output or {}).get("reasoning") or "",
            "verify": vstats,
            "attach": astats,
        }
        return {"findings": attached, "knowledge_refs": refs, "agent_trace": trace}
    except Exception as exc:  # noqa: BLE001 —— Agent 失败不能拖垮报告
        import logging

        logging.getLogger(__name__).warning(
            "agentic_evidence 降级（%s: %s）", type(exc).__name__, str(exc)[:160]
        )
        return {
            "agent_trace": {
                "engine": "agentic",
                "status": "degraded",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
        }
