"""管理后台接口（契约 §6.3）：成本监控、Prompt 版本管理、反馈审核。

指标全部从既有表算出来，不额外维护统计表 —— 保证看板与业务数据永远一致。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AnalysisReport, AnalysisTask, Feedback, PromptTemplate, User, WorkflowStep
from ..schemas import PromptUpsert
from ..serializers import serialize_feedback
from ..services import prompts
from .deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/metrics")
def metrics(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    tasks_total = db.execute(select(func.count()).select_from(AnalysisTask)).scalar_one()
    tasks_done = db.execute(select(func.count()).select_from(AnalysisTask).where(AnalysisTask.status == "done")).scalar_one()
    tasks_failed = db.execute(select(func.count()).select_from(AnalysisTask).where(AnalysisTask.status == "failed")).scalar_one()
    reports_total = db.execute(select(func.count()).select_from(AnalysisReport)).scalar_one()

    # 平均端到端耗时：完成时间 - 创建时间
    durations: list[float] = []
    for created_at, completed_at in db.execute(
        select(AnalysisTask.created_at, AnalysisTask.completed_at).where(AnalysisTask.completed_at.is_not(None))
    ).all():
        if created_at and completed_at:
            durations.append((completed_at - created_at).total_seconds() * 1000)
    avg_latency_ms = int(sum(durations) / len(durations)) if durations else 0

    tokens_total = int(db.execute(select(func.coalesce(func.sum(WorkflowStep.tokens), 0))).scalar_one() or 0)
    cost_total = round(float(db.execute(select(func.coalesce(func.sum(WorkflowStep.cost), 0))).scalar_one() or 0), 4)
    cost_per_report = round(cost_total / reports_total, 4) if reports_total else 0.0

    avg_rating = db.execute(select(func.avg(Feedback.rating))).scalar_one()
    avg_rating = round(float(avg_rating), 2) if avg_rating is not None else None

    step_rows = db.execute(
        select(
            WorkflowStep.step_name,
            func.count(),
            func.avg(WorkflowStep.latency_ms),
            func.coalesce(func.sum(WorkflowStep.cost), 0),
        ).group_by(WorkflowStep.step_name)
    ).all()
    order = ["parse", "jd_match", "ats", "impact", "risk", "suggestion", "report", "qc"]
    step_breakdown = sorted(
        [
            {
                "step_name": name or "",
                "calls": int(calls),
                "avg_latency_ms": int(avg_latency or 0),
                "cost": round(float(cost or 0), 4),
            }
            for name, calls, avg_latency, cost in step_rows
        ],
        key=lambda s: order.index(s["step_name"]) if s["step_name"] in order else 99,
    )

    # 公平性通过率：取各报告 qc 阶段的平均值
    rates: list[float] = []
    for (extra,) in db.execute(select(AnalysisReport.extra_json).limit(500)).all():
        qc = (extra or {}).get("qc") or {}
        rate = qc.get("fairness_pass_rate")
        if isinstance(rate, (int, float)):
            rates.append(float(rate))
    fairness_pass_rate = round(sum(rates) / len(rates), 4) if rates else 1.0

    return {
        "tasks_total": int(tasks_total),
        "tasks_done": int(tasks_done),
        "tasks_failed": int(tasks_failed),
        "reports_total": int(reports_total),
        "avg_latency_ms": avg_latency_ms,
        "tokens_total": tokens_total,
        "cost_total": cost_total,
        "cost_per_report": cost_per_report,
        "feedback_avg_rating": avg_rating,
        "fairness_pass_rate": fairness_pass_rate,
        "step_breakdown": step_breakdown,
    }


@router.get("/prompts")
def list_prompts(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    """返回每个 name 的最新版本 + 当前生效来源（code=代码默认 / db=后台覆盖）。"""
    rows = db.execute(select(PromptTemplate).order_by(PromptTemplate.name, PromptTemplate.version)).scalars().all()
    latest: dict[str, PromptTemplate] = {}
    for row in rows:
        latest[row.name] = row  # 升序遍历，最后一个即最新版本

    items = []
    for name in sorted(set(list(prompts.DEFAULTS.keys()) + list(latest.keys()))):
        row = latest.get(name)
        items.append(
            {
                "name": name,
                "version": row.version if row else 0,
                "updated_at": row.created_at.isoformat() if row and row.created_at else None,
                "source": "db" if row else "code",
                "content_preview": (row.content if row else prompts.DEFAULTS.get(name, ""))[:200],
            }
        )
    return {"items": items}


@router.post("/prompts", status_code=201)
def upsert_prompt(
    payload: PromptUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    current_max = db.execute(
        select(func.coalesce(func.max(PromptTemplate.version), 0)).where(PromptTemplate.name == payload.name)
    ).scalar_one()

    row = PromptTemplate(name=payload.name, version=int(current_max) + 1, content=payload.content)
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "name": row.name,
        "version": row.version,
        "updated_at": row.created_at.isoformat() if row.created_at else None,
        "source": "db",
        "content_preview": (row.content or "")[:200],
    }


@router.get("/feedback")
def list_feedback(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    total = db.execute(select(func.count()).select_from(Feedback)).scalar_one()
    rows = db.execute(select(Feedback).order_by(Feedback.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [serialize_feedback(f) for f in rows], "total": int(total)}
