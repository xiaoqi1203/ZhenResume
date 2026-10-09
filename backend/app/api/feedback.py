"""反馈接口（契约 §6.4）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ReportNotFound
from ..models import AnalysisReport, Feedback, User
from ..schemas import FeedbackCreate
from ..serializers import serialize_feedback
from .deps import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    report = db.get(AnalysisReport, payload.report_id)
    if report is None:
        raise ReportNotFound()

    feedback = Feedback(
        report_id=payload.report_id,
        user_id=user.id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return serialize_feedback(feedback)
