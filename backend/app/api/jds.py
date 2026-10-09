"""JD 接口（契约 §3）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import JdNotFound
from ..models import JobDescription, User
from ..schemas import JdCreate
from ..serializers import serialize_jd
from ..services.parse_rules import parse_jd_text
from .deps import get_current_user

router = APIRouter(prefix="/jds", tags=["jds"])


@router.post("", status_code=201)
def create_jd(
    payload: JdCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    parsed = parse_jd_text(payload.raw_text)
    jd = JobDescription(
        user_id=user.id,
        title=(payload.title or parsed.title or "未命名岗位").strip()[:255],
        company=(payload.company or parsed.company or "").strip()[:255] or None,
        raw_text=payload.raw_text,
        parsed_json=parsed.model_dump(),
    )
    db.add(jd)
    db.commit()
    db.refresh(jd)
    return serialize_jd(jd)


@router.get("")
def list_jds(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    base = select(JobDescription).where(JobDescription.user_id == user.id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(JobDescription.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {
        "items": [
            {
                "jd_id": jd.id,
                "title": jd.title or "",
                "company": jd.company or "",
                "created_at": jd.created_at.isoformat() if jd.created_at else None,
            }
            for jd in rows
        ],
        "total": int(total),
    }


@router.get("/{jd_id}")
def get_jd(
    jd_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    jd = db.get(JobDescription, jd_id)
    if jd is None or jd.user_id != user.id:
        raise JdNotFound()
    return serialize_jd(jd)


@router.delete("/{jd_id}", status_code=204, response_class=Response)
def delete_jd(
    jd_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    jd = db.get(JobDescription, jd_id)
    if jd is None or jd.user_id != user.id:
        raise JdNotFound()
    db.delete(jd)
    db.commit()
    return Response(status_code=204)
