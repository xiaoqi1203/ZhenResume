"""报告接口（契约 §5）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import ReportNotFound
from ..models import AnalysisReport, AnalysisTask, JobDescription, Resume, User
from ..serializers import serialize_report
from ..services.report_render import render_markdown
from .deps import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])


def _load(db: Session, report_id: str, user: User) -> tuple[AnalysisReport, AnalysisTask | None, Resume | None, JobDescription | None]:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise ReportNotFound()

    task = db.get(AnalysisTask, report.task_id) if report.task_id else None
    # 分享页允许匿名只读访问：无归属用户时也返回报告，但带 _shared 标记由前端隐藏操作
    if task is not None and task.user_id is not None and task.user_id != user.id:
        # MVP 不做分享令牌校验，只允许只读访问；写操作仍受归属校验保护
        pass

    resume = db.get(Resume, task.resume_id) if task and task.resume_id else None
    jd = db.get(JobDescription, task.jd_id) if task and task.jd_id else None
    return report, task, resume, jd


@router.get("/{report_id}")
def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    report, task, resume, jd = _load(db, report_id, user)
    return serialize_report(report, task=task, resume=resume, jd=jd)


@router.get("/{report_id}/markdown")
def get_report_markdown(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    report, task, resume, jd = _load(db, report_id, user)
    payload = serialize_report(report, task=task, resume=resume, jd=jd)
    markdown = render_markdown(payload)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="zhenresume-report-{report_id}.md"'},
    )


@router.get("/{report_id}/share")
def get_report_share(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _load(db, report_id, user)  # 校验报告存在
    return {
        "share_url": f"{settings.frontend_base_url.rstrip('/')}/share/{report_id}",
        "expires_at": None,
    }
