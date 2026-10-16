"""LangGraph 编排图（替换手写状态机）。

串行版与并行版共用一个 builder：
- **串行版（默认，行为与现状等价）**：8 个契约节点直线连接；
- **并行版（`WORKFLOW_PARALLEL=true`）**：`jd_match` 扇出到 ats/impact/risk，
  三个节点在同一 superstep 并发，再由 `merge` 按固定顺序归并。

不变量（build 时断言，防止有人把非契约节点写进 workflow_steps）：
- 图里的契约节点集合 == `AGENT_PIPELINE[].name` 集合 == 契约 §4.2 的 8 个 step_name；
- setup / retrieve / merge / finalize / persist **不写** `workflow_steps`。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from langgraph.graph import END, START, StateGraph

from ..config import DATA_DIR, settings
from ..services.llm import get_llm
from .nodes import (
    WorkflowAbort,
    build_agent_nodes,
    finalize_node,
    make_agent_node,
    merge_node,
    parallel_agent_nodes,
    retrieve_node,
    setup_node,
)
from .hr_nodes import hr_summary_node
from .persist import persist_report
from .state import CONTRACT_STEPS, GraphContext, WorkflowState
from .steps import fail_task, mark_task_running, record_step

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 非契约控制节点
# --------------------------------------------------------------------------- #
async def persist_node(state: WorkflowState) -> dict:
    report_id = await asyncio.to_thread(persist_report, state)
    return {"report_id": report_id}


def route_after_qc(state: WorkflowState) -> str:
    """质检后的条件边：回环 or 定稿。

    默认 `WORKFLOW_REWORK_MAX=0` → 永远走 finalize，行为与现状完全一致。
    """
    if settings.workflow_rework_max <= 0:
        return "finalize"
    qc = state.get("qc_report") or {}
    dropped = int(qc.get("dropped") or 0)
    evaluated = max(int(qc.get("evaluated") or 1), 1)
    if dropped / evaluated > 0.5 and int(state.get("rework_count") or 0) < settings.workflow_rework_max:
        return "rework"
    return "finalize"


def rework_node(_state: WorkflowState) -> dict:
    return {"rework_count": int(_state.get("rework_count") or 0) + 1}


# --------------------------------------------------------------------------- #
# 建图
# --------------------------------------------------------------------------- #
def build_graph(*, parallel: bool | None = None, checkpointer=None):
    parallel = settings.workflow_parallel if parallel is None else parallel
    nodes = build_agent_nodes()

    g = StateGraph(WorkflowState, context_schema=GraphContext)
    g.add_node("setup", setup_node)
    g.add_node("retrieve", retrieve_node)

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

    # 边
    g.add_edge(START, "setup")
    g.add_edge("setup", "retrieve")
    g.add_edge("retrieve", "parse")
    g.add_edge("parse", "jd_match")

    if parallel:
        for name in ("ats", "impact", "risk"):
            g.add_edge("jd_match", name)  # 扇出
            g.add_edge(name, "merge")  # 汇聚（等齐三个）
        g.add_edge("merge", "suggestion")
    else:
        g.add_edge("jd_match", "ats")
        g.add_edge("ats", "impact")
        g.add_edge("impact", "risk")
        g.add_edge("risk", "suggestion")

    g.add_edge("suggestion", "report")
    g.add_edge("report", "qc")
    # 注意：qc 只能挂条件边，不能再挂静态边（官方明确警告两者会同时执行）
    # 映射表必须与「实际存在的节点」一致，否则 compile 会报 unknown target。
    qc_routes = {"finalize": "finalize"}
    if settings.workflow_rework_max > 0:
        qc_routes["rework"] = "rework"
    g.add_conditional_edges("qc", route_after_qc, qc_routes)
    if settings.workflow_rework_max > 0:
        g.add_edge("rework", "suggestion")
    g.add_edge("finalize", "persist")
    g.add_edge("persist", END)

    graph = g.compile(checkpointer=checkpointer)

    # 不变量：图里的契约节点必须恰好是那 8 个
    graph_nodes = set(graph.get_graph().nodes)
    contract_in_graph = {n for n in graph_nodes if n in CONTRACT_STEPS}
    assert contract_in_graph == set(CONTRACT_STEPS), contract_in_graph
    assert len(CONTRACT_STEPS) == 8
    return graph


def build_hr_graph(*, parallel: bool | None = None, checkpointer=None):
    """HR 排序表专用图：只要"匹配 + 风险"，砍掉求职者才需要的三个节点。

    串行版：setup → retrieve → parse → jd_match → risk → finalize → hr_summary → persist
    并行版：setup → retrieve → parse ─┬→ jd_match ─┐
                                      └→ risk ────┴→ merge → finalize → hr_summary → persist

    **jd_match 与 risk 互不依赖**（都只吃 parse 的产出），所以可以并行 ——
    实测这两步在串行时占 13.1s + 22.9s，并行后关键路径只剩最大值约 22.9s。

    与完整图的差异（实测每份省约 2/3 时间与 2/3 成本）：
    - **不跑** impact（量化改写）、suggestion（改写建议）、report（求职者总结）；
    - `finalize` 仍用 `compute_scores_deterministic`，所以五维分照常齐全，
      `hr_batches` 的 overall/match/risk 排序与 CSV 导出**契约不变**；
    - `hr_summary` 是确定性文本，不调 LLM。

    注意：这个图只写 3 个契约步（parse / jd_match / risk），
    所以 `workflow_steps` 里不会有 8 行 —— 对 `type=batch` 的任务是允许的
    （契约 §4.2 的 8 步约束针对单份诊断任务；`serialize_task` 会按 STEP_ORDER 补 pending 行）。
    """
    parallel = settings.workflow_parallel if parallel is None else parallel
    nodes = build_agent_nodes()

    g = StateGraph(WorkflowState, context_schema=GraphContext)
    g.add_node("setup", setup_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("parse", nodes["parse"])

    if parallel:
        # 两个节点都按"批次"写发现项，由 merge 归并（顺序固定为 jd_match → risk）
        g.add_node("jd_match", make_agent_node(_agent_cls("jd_match"), parallel_safe=True))
        g.add_node("risk", make_agent_node(_agent_cls("risk"), parallel_safe=True))
        g.add_node("merge", merge_node)
    else:
        g.add_node("jd_match", nodes["jd_match"])
        g.add_node("risk", nodes["risk"])

    g.add_node("finalize", finalize_node)          # 定稿五维分（确定性）
    g.add_node("hr_summary", hr_summary_node)      # 确定性摘要 + 知识库引用
    g.add_node("persist", persist_node)

    g.add_edge(START, "setup")
    g.add_edge("setup", "retrieve")
    g.add_edge("retrieve", "parse")
    if parallel:
        g.add_edge("parse", "jd_match")            # 扇出
        g.add_edge("parse", "risk")
        g.add_edge("jd_match", "merge")            # 汇聚（等齐两个）
        g.add_edge("risk", "merge")
        g.add_edge("merge", "finalize")
    else:
        g.add_edge("parse", "jd_match")
        g.add_edge("jd_match", "risk")
        g.add_edge("risk", "finalize")
    g.add_edge("finalize", "hr_summary")
    g.add_edge("hr_summary", "persist")
    g.add_edge("persist", END)

    graph = g.compile(checkpointer=checkpointer)

    graph_nodes = set(graph.get_graph().nodes)
    assert {"parse", "jd_match", "risk"} <= graph_nodes
    assert not ({"impact", "suggestion", "report"} & graph_nodes), "HR 图不应包含详细分析节点"
    return graph


# --------------------------------------------------------------------------- #
# checkpointer：按任务构建（避开"全局 async saver + asyncio.run"的事件循环绑定坑）
# --------------------------------------------------------------------------- #
def _to_psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def make_checkpointer():
    mode = settings.langgraph_checkpoint
    if mode == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.langgraph_checkpoint_url or str(DATA_DIR / "zhenresume_checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            await saver.setup()  # 建表；幂等
            yield saver
    elif mode == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        dsn = settings.langgraph_checkpoint_url or _to_psycopg_dsn(settings.database_url)
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await saver.setup()
            yield saver
    else:
        yield None


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
async def run_graph(task_id: str, *, mode: str = "full") -> str | None:
    """后台任务入口。`mode="screening"` 时走 HR 排序表图（轻量，砍掉详细分析）。

    `mode="auto"` 时按任务类型决定：
    - `analysis_tasks.type == "batch"` → HR 排序表（HR 批量初筛）；
    - 其它（`single`，包含编辑器发起的诊断）→ 完整 8 节点流水线。
    """
    if mode == "auto":
        mode = "screening" if (settings.hr_screening and _is_batch_task(task_id)) else "full"

    await mark_task_running(task_id)

    async with make_checkpointer() as checkpointer:
        graph = build_hr_graph(checkpointer=checkpointer) if mode == "screening" else build_graph(checkpointer=checkpointer)
        context = GraphContext(
            llm=get_llm(),
            semaphore=asyncio.Semaphore(max(1, settings.llm_max_concurrency)),
        )
        invoke_kwargs: dict = {
            # Python 3.10 + async：必须显式把 config 传进 ainvoke（官方已知限制）
            "config": {"configurable": {"thread_id": task_id}},
            "context": context,
        }
        if settings.langgraph_durability:
            invoke_kwargs["durability"] = settings.langgraph_durability
        try:
            try:
                final = await graph.ainvoke({"task_id": task_id}, **invoke_kwargs)
            except TypeError:
                # 老版本没有 durability 参数 → 去掉再试一次
                invoke_kwargs.pop("durability", None)
                final = await graph.ainvoke({"task_id": task_id}, **invoke_kwargs)
        except WorkflowAbort as exc:
            fail_task(task_id, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            fail_task(task_id, f"工作流异常终止：{type(exc).__name__}: {str(exc)[:200]}")
            raise

    report_id = final.get("report_id")
    if not report_id:
        fail_task(task_id, "工作流未产出报告")
        return None
    logger.info("任务 %s 完成，报告 %s", task_id, report_id)
    return report_id


def _agent_cls(name: str):
    """按 name 取 Agent 类（用于给 HR 图单独构造并行安全节点）。"""
    from ..agents import AGENT_PIPELINE

    return next(c for c in AGENT_PIPELINE if c.name == name)


def _is_batch_task(task_id: str) -> bool:
    """任务是否来自 HR 批量初筛（据此选择轻量图）。"""
    from ..db import session_scope
    from ..models import AnalysisTask

    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        return bool(task and task.type == "batch")


async def resume_zombies() -> int:
    """把卡在 running 且心跳超时的任务从断点续跑（服务重启后调用）。

    心跳 = 该任务最近一行 workflow_steps 的 created_at（用现有列，无需加字段）。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from ..db import session_scope
    from ..models import AnalysisTask, WorkflowStep

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.zombie_task_timeout_s)
    with session_scope() as db:
        last_step = (
            select(WorkflowStep.task_id, func.max(WorkflowStep.created_at).label("hb"))
            .group_by(WorkflowStep.task_id)
            .subquery()
        )
        rows = (
            db.execute(
                select(AnalysisTask.id)
                .outerjoin(last_step, last_step.c.task_id == AnalysisTask.id)
                .where(
                    AnalysisTask.status == "running",
                    func.coalesce(last_step.c.hb, AnalysisTask.created_at) < cutoff,
                )
            )
            .scalars()
            .all()
        )

    for task_id in rows:
        logger.warning("回收僵尸任务 %s（从断点续跑）", task_id)
        try:
            await run_graph(task_id, mode="auto")
        except Exception:  # noqa: BLE001
            logger.exception("僵尸任务 %s 续跑失败", task_id)
    return len(rows)
