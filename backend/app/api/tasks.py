"""分析任务接口（契约 §4）—— 工作流的入口与轮询出口。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import JdNotFound, QuotaExceeded, ResumeNotFound, TaskNotFound
from ..models import AnalysisReport, AnalysisTask, JobDescription, Payment, Resume, User
from ..schemas import TaskCreate
from ..serializers import serialize_task, serialize_task_summary
from ..workflow import launch_workflow
from .deps import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _has_paid_access(db: Session, user: User) -> bool:
    """是否存在有效付费记录（MVP 只判断有没有成功支付过）。"""
    paid = db.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == user.id, Payment.status == "paid")
    ).scalar_one()
    return bool(paid)


@router.post("", status_code=202)
def create_task(
    payload: TaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    resume = db.get(Resume, payload.resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()
    jd = db.get(JobDescription, payload.jd_id)
    if jd is None or jd.user_id != user.id:
        raise JdNotFound()

    # 免费额度闸门（aaa.md §3.7：免费 3 次基础诊断）
    if user.free_quota_used >= settings.free_quota and not _has_paid_access(db, user):
        raise QuotaExceeded(f"免费额度 {settings.free_quota} 次已用完，请选择付费套餐继续诊断")

    task = AnalysisTask(
        user_id=user.id,
        resume_id=resume.id,
        jd_id=jd.id,
        type="single",
        status="pending",
        progress=0.0,
    )
    db.add(task)
    user.free_quota_used = (user.free_quota_used or 0) + 1
    db.commit()
    db.refresh(task)

    # 异步执行：Starlette 会 await 这个 async 函数，请求立即返回 202
    background_tasks.add_task(launch_workflow, task.id)

    return {"task_id": task.id, "status": task.status}


@router.get("")
def list_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    base = select(AnalysisTask).where(AnalysisTask.user_id == user.id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(AnalysisTask.created_at.desc()).limit(limit).offset(offset)).scalars().all()

    report_by_task = {
        r.task_id: r.id
        for r in db.execute(select(AnalysisReport).where(AnalysisReport.task_id.in_([t.id for t in rows]))).scalars().all()
    } if rows else {}

    return {"items": [serialize_task_summary(t, report_by_task.get(t.id)) for t in rows], "total": int(total)}


@router.get("/{task_id}")
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    task = db.get(AnalysisTask, task_id)
    if task is None or task.user_id != user.id:
        raise TaskNotFound()

    report_id = db.execute(select(AnalysisReport.id).where(AnalysisReport.task_id == task.id)).scalar_one_or_none()
    return serialize_task(task, steps=list(task.steps), report_id=report_id)
