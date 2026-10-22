"""报告接口（契约 §5）+ 分享链接（token 化、可撤销、7 天有效）。"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import ReportNotFound
from ..models import AnalysisReport, AnalysisTask, JobDescription, ReportShare, Resume, User
from ..schemas import ShareCreate
from ..serializers import serialize_report
from ..services.benchmark import build_benchmark
from ..services.report_render import render_markdown
from .deps import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])
# 公开接口：不校验设备归属，只认 token
shares_router = APIRouter(prefix="/shares", tags=["shares"])

SHARE_TTL_DAYS = 7
TOKEN_BYTES = 24  # token_urlsafe(24) ≈ 32 字符，足够不可猜测

# 分享内容里的联系方式打码：即便用户勾选了"包含原文"，手机号/邮箱也不外泄
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _mask(text: str | None) -> str:
    """把手机号、邮箱打码（保留可读性）。"""
    if not text:
        return ""
    masked = _PHONE_RE.sub(r"\1****\2", text)
    return _EMAIL_RE.sub(r"\1***\2", masked)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


@router.api_route("/{report_id}/share", methods=["GET", "POST"])
def create_report_share(
    report_id: str,
    payload: ShareCreate | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """生成分享链接（默认 7 天有效、可撤销）。

    - 每次调用生成新 token（库里只存 hash，明文不落库）；
    - `include_raw=true` 时公开页才会带简历原文引用，默认不带。

    ⚠️ 同时接受 GET 与 POST：契约 §5.2 写的是 `GET /reports/{id}/share`，
    而实现曾经只有 POST —— 照契约对接的客户端会拿到 405（`scripts/verify.py`
    的「分享链接返回 200」就是因此长期失败）。生成链接是幂等且无副作用的读操作，
    对企业代理与浏览器直达也更友好，所以按契约保留 GET；POST 继续兼容老前端。
    """
    report, _task, _resume, _jd = _load(db, report_id, user)
    include_raw = bool(payload.include_raw) if payload else False

    token = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SHARE_TTL_DAYS)
    share = ReportShare(
        report_id=report.id,
        token_hash=_hash_token(token),
        include_raw=include_raw,
        expires_at=expires_at,
    )
    db.add(share)
    db.commit()

    base = settings.frontend_base_url.rstrip("/")
    return {
        "share_url": f"{base}/s/{token}",
        "expires_at": expires_at.isoformat(),
        "include_raw": include_raw,
        "ttl_days": SHARE_TTL_DAYS,
    }


@router.delete("/{report_id}/share", status_code=204)
def revoke_report_share(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """撤销这份报告的所有分享链接（立即失效）。"""
    _load(db, report_id, user)
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(ReportShare).where(ReportShare.report_id == report_id, ReportShare.revoked_at.is_(None))
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    db.commit()
    return Response(status_code=204)


@shares_router.get("/{token}")
def get_shared_report(token: str, db: Session = Depends(get_db)) -> dict:
    """公开读取分享内容（无需设备 ID）。

    内容按「结论卡」裁剪：分数、总评、风险概览、问题标题/说明/建议；
    **不返回**简历原文、改写句、证据引用 —— 除非创建分享时显式勾选了 include_raw。
    联系方式一律打码。
    """
    share = db.execute(
        select(ReportShare).where(ReportShare.token_hash == _hash_token(token))
    ).scalar_one_or_none()
    if share is None or share.revoked_at is not None:
        raise HTTPException(status_code=404, detail={"code": "SHARE_NOT_FOUND", "message": "分享链接不存在或已撤销"})
    now = datetime.now(timezone.utc)
    expires_at = share.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:  # SQLite 读回来可能是 naive
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            raise HTTPException(status_code=410, detail={"code": "SHARE_EXPIRED", "message": "分享链接已过期"})

    report = db.get(AnalysisReport, share.report_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"code": "REPORT_NOT_FOUND", "message": "报告不存在"})

    task = db.get(AnalysisTask, report.task_id) if report.task_id else None
    resume = db.get(Resume, task.resume_id) if task and task.resume_id else None
    jd = db.get(JobDescription, task.jd_id) if task and task.jd_id else None
    full = serialize_report(report, task=task, resume=resume, jd=jd)

    include_raw = bool(share.include_raw)
    findings = []
    for item in full["findings"]:
        entry = {
            "finding_id": item["finding_id"],
            "category": item["category"],
            "severity": item["severity"],
            "title": _mask(item["title"]),
            "issue": _mask(item["issue"]),
            "suggestion": _mask(item["suggestion"]),
        }
        if include_raw:
            entry["original_text"] = _mask(item.get("original_text"))
            entry["rewritten_text"] = _mask(item.get("rewritten_text"))
            entry["evidence"] = item.get("evidence")
        findings.append(entry)

    share.view_count = (share.view_count or 0) + 1
    db.commit()

    return {
        "report_id": full["report_id"],
        "created_at": full["created_at"],
        "resume_title": full["resume_title"],
        "jd_title": full["jd_title"],
        "scores": full["scores"],
        "risk_level": full["risk_level"],
        "summary": _mask(full["summary"]),
        "findings": findings,
        "missing_keywords": full["missing_keywords"],
        "action_items": [_mask(item) for item in full["action_items"]],
        "disclaimer": full["disclaimer"],
        "include_raw": include_raw,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "view_count": share.view_count,
    }
