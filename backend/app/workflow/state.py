"""LangGraph 工作流状态与 AgentContext 适配。

三条铁律（来自 8 个 Agent 的实现事实）：
1. `findings` / `missing_keywords` / ... 是**整体覆盖**语义（`jd_match`、`suggestion`、`qc`
   三处都是 `ctx.findings = [...]`），所以**不加 reducer**。
   只有并行节点各自追加的字段才加 reducer。
2. 同一 superstep 内多个节点不得写同一个无 reducer 的 channel
   （LangGraph 会抛 `InvalidUpdateError`）→ 每个节点只声明自己负责的字段
   （见 `NODE_STATE_FIELDS`）。
3. state 必须可 JSON 序列化：Session / httpx client / LLMClient 一律放 `GraphContext`，
   不进 checkpoint。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any

from typing_extensions import TypedDict

from ..agents.base import AgentContext
from ..schemas import ParsedJd, ParsedResume

# 契约 §4.2 的 8 个 step_name，顺序即契约
CONTRACT_STEPS: tuple[str, ...] = (
    "parse",
    "jd_match",
    "ats",
    "impact",
    "risk",
    "suggestion",
    "report",
    "qc",
)

# 并行扇出的三个节点（完整图用）
PARALLEL_NODES: tuple[str, ...] = ("ats", "impact", "risk")
MERGE_ORDER: dict[str, int] = {name: i for i, name in enumerate(PARALLEL_NODES)}

# 归并时的**全局固定顺序**（两个图共用）：
# 完整图扇出 ats/impact/risk，HR 图扇出 jd_match/risk —— 相对顺序都保持与串行流水线一致，
# 这样发现项顺序不会随调度抖动（回归断言按顺序比对）。
MERGE_SEQUENCE: dict[str, int] = {"jd_match": 0, "ats": 1, "impact": 2, "risk": 3}

# 每个节点允许写回 state 的字段（唯一真源，可被单测断言）。
# ats/impact/risk 只做 append：串行版由 make_agent_node 额外补一条"整体写入 findings"
# （与现状 engine 循环完全等价）；并行版只写 finding_batches，绝不写 findings。
NODE_STATE_FIELDS: dict[str, tuple[str, ...]] = {
    "parse": ("parsed_resume", "parsed_jd", "role_family"),
    "jd_match": ("findings", "missing_keywords", "covered_keywords"),
    "ats": (),
    "impact": (),
    "risk": (),
    "suggestion": ("findings", "action_items"),
    "report": ("scores", "knowledge_refs", "summary"),
    "qc": ("findings", "qc_report"),
}


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """字典字段的 reducer：按 key 合并，后写覆盖同 key（outputs 用）。"""
    return {**(left or {}), **(right or {})}


class StepRecord(TypedDict, total=False):
    step_name: str
    status: str  # running | done | degraded | failed
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
    outputs: Annotated[dict, merge_dict]  # 并行节点各写自己那一个 key
    finding_batches: Annotated[list, operator.add]  # 并行节点各 append 一个批次
    steps: Annotated[list, operator.add]  # 审计 / 用量回流

    # --- 控制与产出 ---
    report_id: str | None
    rework_count: int


@dataclass
class GraphContext:
    """不进 checkpoint 的运行时依赖（LangGraph 1.x 的 context API）。"""

    llm: Any = None
    retriever: Any = None
    semaphore: Any = None  # asyncio.Semaphore，限制并发 LLM 调用


def safe_resume(raw: dict | None) -> ParsedResume:
    try:
        return ParsedResume.model_validate(raw or {})
    except Exception:  # noqa: BLE001
        return ParsedResume.empty()


def safe_jd(raw: dict | None) -> ParsedJd:
    try:
        return ParsedJd.model_validate(raw or {})
    except Exception:  # noqa: BLE001
        return ParsedJd.empty()


def ctx_from_state(state: WorkflowState) -> AgentContext:
    """state → AgentContext。与现状 engine.py 的构造保持一致。"""
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
    """按 NODE_STATE_FIELDS 取该节点负责的字段。**并行安全的关键**。"""
    out: dict = {}
    for field_name in NODE_STATE_FIELDS.get(agent_name, ()):
        value = getattr(ctx, field_name)
        if field_name in ("parsed_resume", "parsed_jd"):
            value = value.model_dump()
        out[field_name] = value
    return out


def delta_findings(before: list, after: list) -> list:
    """取节点新增的发现项（并行版用）。

    ats / impact / risk 只做 `ctx.add_findings()`（append），所以前缀不变；
    万一将来有人改成替换，这里退化为"按内容差集"，不会静默丢条目。
    """
    if len(after) >= len(before) and after[: len(before)] == before:
        return list(after[len(before) :])
    seen = {repr(item) for item in before}
    return [item for item in after if repr(item) not in seen]
