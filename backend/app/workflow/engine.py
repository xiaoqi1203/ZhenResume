"""工作流引擎：手写状态机，替代 LangGraph（MVP-lite 的核心简化）。

为什么可以不用 LangGraph：
8 个节点是一条确定的线性链，没有分支、没有循环、没有人机回环，LangGraph 的
checkpoint / 状态图 / 可视化能力在这里基本用不上，却会引入额外依赖与调试成本。

但接口刻意与 LangGraph 对齐，升级时只需替换本文件：
- 每个节点是 `async (state, llm) -> partial_state`；
- 节点间通过显式状态对象（AgentContext）传递，不靠隐式全局变量；
- 每次执行都落一行 `workflow_steps`，input/output/tokens/cost/latency 全部可观测。

两个关键设计决策：
1. **QC 内部定稿**（详见 `agents/qc_agent.py::_finalize`）：质检会剔除/降级发现项，
   分数、风险等级与摘要统一由 `analyze.finalize_report` 在 qc 之后算**一次**并写回 ctx；
   本文件只负责落库，绝不再算第二遍（两处计算是历史缺陷的根因）。
2. **input 只存摘要**：不把简历全文写进 workflow_steps，避免数据库膨胀与隐私外泄。
"""

from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..agents import AGENT_PIPELINE
from ..agents.base import AgentContext
from ..agents.report_agent import DISCLAIMER
from ..config import settings
from ..db import session_scope
from ..models import AnalysisReport, AnalysisTask, JobDescription, ReportFinding, Resume, WorkflowStep
from ..schemas import ParsedJd, ParsedResume
from ..services import analyze, prompts, rag
from ..services.llm import get_llm

TOTAL_STEPS = len(AGENT_PIPELINE)


def launch_workflow(task_id: str) -> None:
    """后台任务入口（供 FastAPI BackgroundTasks 调用）。异常全部吞掉并落到任务状态里。"""
    try:
        asyncio.run(run_analysis(task_id))
    except Exception:  # noqa: BLE001 后台任务不能再往外抛，否则会静默丢失 ——
        traceback.print_exc()
        try:  # 兜底：不留 running 僵尸（LangGraph 路径自己会置 failed，legacy 也要保证）
            from .steps import fail_task

            fail_task(task_id, "工作流异常终止")
        except Exception:  # noqa: BLE001
            pass


async def run_analysis(task_id: str) -> str | None:
    """按 `WORKFLOW_ENGINE` 分派：langgraph = 图编排；legacy = 本文件的手写状态机。

    两者共用同一批 `services/*` 与 `agents/*`，所以切换只改变"谁来编排"，
    不改变业务语义（分数、发现项、证据链、成本口径完全一致）。

    LangGraph 路径还会按**任务类型**自动选图（见 graph.run_graph 的 mode="auto"）：
    - `type=batch`（HR 批量初筛）→ **HR 排序表轻量图**（parse + jd_match + risk）；
    - 其它（求职者单份诊断）→ 完整 8 节点流水线（详细分析保留）。
    """
    if settings.workflow_engine == "langgraph":
        from .graph import run_graph

        # 按任务选择分析模式：前端提交时可选 fixed（快）/ agentic（深度）。
        # 老库没有 mode 列时取不到 → 回落到全局默认。
        task_mode = _task_workflow_mode(task_id)
        return await run_graph(task_id, mode="auto", workflow_mode=task_mode)
    return await run_workflow(task_id)


def _task_workflow_mode(task_id: str) -> str | None:
    """读任务的 fixed / agentic 选择；读不到就返回 None（用全局默认）。"""
    try:
        with session_scope() as db:
            task = db.get(AnalysisTask, task_id)
            mode = (getattr(task, "mode", None) or "").strip().lower()
            return mode if mode in {"fixed", "agentic"} else None
    except Exception:  # noqa: BLE001
        return None


async def run_workflow(task_id: str) -> str | None:
    """执行完整工作流，返回 report_id；失败时任务状态置为 failed。"""
    llm = get_llm()

    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return None
        resume = db.get(Resume, task.resume_id) if task.resume_id else None
        jd = db.get(JobDescription, task.jd_id) if task.jd_id else None
        if resume is None or jd is None:
            _fail(db, task, "简历或 JD 记录缺失")
            return None

        task.status = "running"
        task.progress = 0.0
        task.error = None
        db.flush()

        ctx = AgentContext(
            task_id=task_id,
            resume_text=resume.raw_text or "",
            jd_text=jd.raw_text or "",
            parsed_resume=_safe_resume(resume.parsed_json),
            parsed_jd=_safe_jd(jd.parsed_json),
            prompt_overrides=prompts.load_overrides(db),
            # legacy 手写状态机只跑 fixed 线（agentic 只在 LangGraph 图里）。
            # 显式写明而不是靠默认值：双分制的开关判据是 workflow_mode，
            # 靠隐式默认值会在将来接 agentic 时静默不出深度分。
            workflow_mode="fixed",
        )

        # RAG 检索：一次检索，全链路复用，避免每个 Agent 重复打库
        # ⚠️ 传 resume_text：原实现只拼技能栏，技能栏相同的简历会得到逐字相同的
        # query（见 rag.build_query 的 docstring）。
        query = rag.build_query(
            ctx.parsed_jd.model_dump(),
            ctx.parsed_resume.model_dump(),
            raw_text=ctx.resume_text,
        )
        if query.strip():
            ctx.knowledge_hits = rag.search(db, query, top_k=5)

        report_id: str | None = None

        for index, agent_cls in enumerate(AGENT_PIPELINE, start=1):
            agent = agent_cls()
            step = WorkflowStep(
                task_id=task_id,
                step_name=agent.name,
                status="running",
                input_json=_input_digest(agent.name, ctx),
            )
            db.add(step)
            task.current_step = agent.name
            db.flush()

            started = time.perf_counter()
            try:
                output = await agent.safe_run(ctx, llm)
                if output.status == "degraded":
                    step.status = "degraded"
                else:
                    step.status = "done"
                step.notes = output.notes
            except Exception as exc:  # noqa: BLE001 —— critical Agent 失败即终止整个任务
                step.status = "failed"
                step.latency_ms = int((time.perf_counter() - started) * 1000)
                step.notes = f"{type(exc).__name__}: {str(exc)[:300]}"
                _fail(db, task, f"{agent.title} 执行失败：{str(exc)[:200]}")
                return None

            step.latency_ms = output.latency_ms or int((time.perf_counter() - started) * 1000)
            step.tokens = output.tokens
            step.cost = output.cost
            step.output_json = _truncate(output.data)

            task.progress = round(index / TOTAL_STEPS, 4)
            db.flush()

            if agent.name == "qc":
                # QC 已经在内部定稿了分数 / 风险等级 / 摘要（`analyze.finalize_report`），
                # 这里只落库。**不要**再算一遍 —— 两处计算正是「85 分 + 综合得分 84 分」
                # 与「3 条红 + yellow」这两个自相矛盾的根因。
                report = _persist_report(db, task, ctx)
                report_id = report.id

        if report_id is None:
            _fail(db, task, "工作流未产出报告")
            return None

        task.status = "done"
        task.progress = 1.0
        task.current_step = "qc"
        task.completed_at = datetime.now(timezone.utc)
        db.flush()

        return report_id


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _fail(db: Session, task: AnalysisTask, message: str) -> None:
    task.status = "failed"
    task.error = message[:500]
    task.completed_at = datetime.now(timezone.utc)
    db.flush()


def _safe_resume(raw: dict | None) -> ParsedResume:
    try:
        return ParsedResume.model_validate(raw or {})
    except Exception:  # noqa: BLE001
        return ParsedResume.empty()


def _safe_jd(raw: dict | None) -> ParsedJd:
    try:
        return ParsedJd.model_validate(raw or {})
    except Exception:  # noqa: BLE001
        return ParsedJd.empty()


def _persist_report(db: Session, task: AnalysisTask, ctx: AgentContext) -> AnalysisReport:
    scores = ctx.scores
    report = AnalysisReport(
        task_id=task.id,
        overall_score=scores.get("overall"),
        ats_score=scores.get("ats"),
        impact_score=scores.get("impact"),
        clarity_score=scores.get("clarity"),
        match_score=scores.get("match"),
        risk_score=scores.get("risk"),
        # 风险等级取 qc 的定稿结论（吸收全维度红色）；没有时按最终清单派生。
        risk_level=ctx.risk_level or analyze.risk_level_from_findings(int(scores.get("risk") or 0), ctx.findings),
        summary=ctx.summary,
        report_url=None,
        extra_json={
            "missing_keywords": ctx.missing_keywords[:20],
            "covered_keywords": ctx.covered_keywords[:30],
            "action_items": ctx.action_items,
            "knowledge_refs": ctx.knowledge_refs,
            "qc": ctx.qc_report,
            "disclaimer": DISCLAIMER,
            "agent_outputs": {k: v for k, v in ctx.outputs.items() if k != "parse"},
        },
    )
    db.add(report)
    db.flush()

    for order, finding in enumerate(analyze.sort_findings(ctx.findings)):
        db.add(
            ReportFinding(
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
            )
        )
    db.flush()
    return report


def _input_digest(agent_name: str, ctx: AgentContext) -> dict:
    """只存摘要：既便于排查，又不会把简历全文写进库。"""
    return {
        "agent": agent_name,
        "resume_chars": len(ctx.resume_text),
        "jd_chars": len(ctx.jd_text),
        "work_count": len(ctx.parsed_resume.work),
        "project_count": len(ctx.parsed_resume.projects),
        "bullet_count": len(ctx.parsed_resume.bullets()),
        "skill_count": len(ctx.parsed_resume.skills),
        "jd_skill_count": len(ctx.parsed_jd.all_skills()),
        "knowledge_hits": len(ctx.knowledge_hits),
        "findings_so_far": len(ctx.findings),
        "role_family": ctx.role_family,
    }


def _truncate(data: dict | None, limit: int = 4000) -> dict:
    """裁掉过大的输出，避免单行 JSON 过大；保留结构，长文本截断。"""
    if not data:
        return {}

    def cut(value):
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "…[truncated]"
        if isinstance(value, list):
            return [cut(v) for v in value[:30]]
        if isinstance(value, dict):
            return {k: cut(v) for k, v in list(value.items())[:40]}
        return value

    return cut(data)
