"""报告落库（幂等）。

为什么单独一个文件：LangGraph 的节点会被**重放**（崩溃恢复、time travel），
所以落库必须按 `task_id` upsert、发现项先删后插 —— 重放不会产生第二份报告或重复发现项。
字段顺序与 `engine._persist_report` 逐一对应，这是 `serializers.serialize_report`
与前端报告页的依赖，不能改。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..agents.report_agent import DISCLAIMER
from ..db import session_scope
from ..models import AnalysisReport, AnalysisTask, ReportFinding
from ..services import analyze
from .state import WorkflowState, ctx_from_state


def persist_report(state: WorkflowState) -> str:
    ctx = ctx_from_state(state)
    scores = ctx.scores
    task_id = state["task_id"]

    with session_scope() as db:
        report = db.execute(
            select(AnalysisReport).where(AnalysisReport.task_id == task_id)
        ).scalar_one_or_none()
        if report is None:
            report = AnalysisReport(task_id=task_id)
            db.add(report)
            db.flush()

        # 先删后插：重放安全
        db.query(ReportFinding).filter(ReportFinding.report_id == report.id).delete(
            synchronize_session=False
        )

        report.overall_score = scores.get("overall")
        report.ats_score = scores.get("ats")
        report.impact_score = scores.get("impact")
        report.clarity_score = scores.get("clarity")
        report.match_score = scores.get("match")
        report.risk_score = scores.get("risk")
        report.risk_level = analyze.risk_level_from_score(int(scores.get("risk") or 0))
        report.summary = ctx.summary
        report.report_url = None
        report.extra_json = {
            "missing_keywords": ctx.missing_keywords[:20],
            "covered_keywords": ctx.covered_keywords[:30],
            "action_items": ctx.action_items,
            "knowledge_refs": ctx.knowledge_refs,
            "qc": ctx.qc_report,
            "disclaimer": DISCLAIMER,
            "agent_outputs": {k: v for k, v in ctx.outputs.items() if k != "parse"},
        }
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

        task = db.get(AnalysisTask, task_id)
        if task is not None:
            task.status = "done"
            task.progress = 1.0
            task.current_step = "qc"
            task.completed_at = datetime.now(timezone.utc)
        db.flush()
        report_id = report.id

    return report_id
