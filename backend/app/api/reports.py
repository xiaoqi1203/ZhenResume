"""报告接口（契约 §5）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import ReportNotFound
from ..models import AnalysisReport, AnalysisTask, JobDescription, Resume, User
from ..serializers import serialize_report
from ..services.benchmark import build_benchmark
from ..services.report_render import render_markdown
from .deps import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])


def _load(db: Session, report_id: str, user: User) -> tuple[AnalysisReport, AnalysisTask | None, Resume | None, JobDescription | None]:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise ReportNotFound()

    task = db.get(AnalysisTask, report.task_id) if report.task_id else None
    # ⚠️ 归属校验（契约 §0.1 明确要求"归属校验"）。
    #
    # 原实现这里是个 `pass`，注释写的是"分享页允许匿名只读访问，MVP 不做分享令牌校验"。
    # 但它带来的是**读接口的 IDOR**：任何知道 report_id 的人都能读到别人的报告
    # （报告里含简历原文引用、分数、风险结论，属于个人信息）。而且注释里说的
    # `_shared` 标记根本没实现 —— 等于既没校验也没提示。
    #
    # 分享功能按既定计划延后实现；等真正做分享时，必须走**单独的令牌白名单入口**
    # （一次性 token / 有效期 / 可撤销），而不是在这里"放行所有人"。
    if task is not None and task.user_id is not None and task.user_id != user.id:
        raise ReportNotFound()

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
    # 同岗位对标（契约 §5.3）：读聚合表 role_stats（已过 k≥10 闸门），
    # 不读任何人的简历原文 —— 所以对求职者展示不需要额外授权。
    # 返回 None 时（样本不足 / 岗位族未知）该字段为 null，前端整块隐藏。
    benchmark = build_benchmark(db, resume, jd) if resume else None
    return serialize_report(report, task=task, resume=resume, jd=jd, benchmark=benchmark)


@router.get("/{report_id}/markdown")
def get_report_markdown(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    report, task, resume, jd = _load(db, report_id, user)
    benchmark = build_benchmark(db, resume, jd) if resume else None
    payload = serialize_report(report, task=task, resume=resume, jd=jd, benchmark=benchmark)
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
