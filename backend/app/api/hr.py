"""HR 批量初筛接口（契约 §6.5，对应 aaa.md §3.9）。

实现方式：每份简历创建一个独立 AnalysisTask 并复用同一套 8-Agent 工作流，
这样单份报告与批量报告的质量、字段、可追溯性完全一致，不会出现"批量版缩水"。

**MVP 已知简化（诚实标注）**：
- 批量任务不做并发控制，串行执行；100 份简历会跑较久，生产环境应换成 Redis + Celery 并发。
- 批量不消耗免费额度也不校验 HR 套餐配额，配额逻辑留在 tasks 接口。
"""

from __future__ import annotations

import asyncio
import csv
import io
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, session_scope
from ..errors import BatchNotFound, JdNotFound, ResumeNotFound, ValidationError
from ..models import AnalysisReport, AnalysisTask, HrBatch, HrBatchItem, JobDescription, Resume, User
from ..schemas import BatchCreate
from ..serializers import serialize_batch, serialize_batch_item
from ..workflow import run_workflow
from .deps import get_current_user

router = APIRouter(prefix="/hr", tags=["hr"])

RISK_LABEL = {"red": "高风险", "yellow": "需关注", "green": "良好"}


def _run_batch(batch_id: str) -> None:
    try:
        asyncio.run(_process_batch(batch_id))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


async def _process_batch(batch_id: str) -> None:
    with session_scope() as db:
        batch = db.get(HrBatch, batch_id)
        if batch is None:
            return
        batch.status = "running"
        item_ids = [item.id for item in batch.items]
        jd_id = batch.jd_id
        user_id = batch.user_id
        db.flush()

    for item_id in item_ids:
        with session_scope() as db:
            item = db.get(HrBatchItem, item_id)
            if item is None:
                continue
            task = AnalysisTask(
                user_id=user_id,
                resume_id=item.resume_id,
                jd_id=jd_id,
                type="batch",
                status="pending",
                progress=0.0,
            )
            db.add(task)
            db.flush()
            item.task_id = task.id
            item.status = "running"
            task_id = task.id

        report_id = await run_workflow(task_id)

        with session_scope() as db:
            item = db.get(HrBatchItem, item_id)
            batch = db.get(HrBatch, batch_id)
            report = db.get(AnalysisReport, report_id) if report_id else None
            if item is not None:
                item.report_id = report_id
                if report is not None:
                    item.status = "done"
                    item.overall_score = report.overall_score
                    item.match_score = report.match_score
                    item.risk_level = report.risk_level
                    item.summary = report.summary
                else:
                    item.status = "failed"
            if batch is not None:
                batch.finished = (batch.finished or 0) + 1
                if batch.finished >= (batch.total or 0):
                    batch.status = "done"
            db.flush()


@router.post("/batches", status_code=202)
def create_batch(
    payload: BatchCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    jd = db.get(JobDescription, payload.jd_id)
    if jd is None or jd.user_id != user.id:
        raise JdNotFound()

    resumes: list[Resume] = []
    for resume_id in payload.resume_ids:
        resume = db.get(Resume, resume_id)
        if resume is None or resume.user_id != user.id:
            raise ResumeNotFound(f"简历不存在：{resume_id}")
        resumes.append(resume)

    if not resumes:
        raise ValidationError("至少需要一份简历")

    batch = HrBatch(user_id=user.id, jd_id=jd.id, status="pending", total=len(resumes), finished=0)
    db.add(batch)
    db.flush()

    for resume in resumes:
        db.add(HrBatchItem(batch_id=batch.id, resume_id=resume.id, status="pending"))
    db.commit()
    db.refresh(batch)

    background_tasks.add_task(_run_batch, batch.id)

    return {"batch_id": batch.id, "status": batch.status, "total": batch.total}


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    batch = db.get(HrBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise BatchNotFound()

    resumes = {
        r.id: r
        for r in db.execute(select(Resume).where(Resume.id.in_([i.resume_id for i in batch.items]))).scalars().all()
    }
    items = [serialize_batch_item(item, resumes.get(item.resume_id)) for item in batch.items]
    # 契约要求按总分降序；未完成的排在最后
    items.sort(key=lambda i: (i["overall_score"] is None, -(i["overall_score"] or 0)))
    return serialize_batch(batch, items=items)


@router.get("/batches/{batch_id}/export.csv")
def export_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    batch = db.get(HrBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise BatchNotFound()

    resumes = {
        r.id: r
        for r in db.execute(select(Resume).where(Resume.id.in_([i.resume_id for i in batch.items]))).scalars().all()
    }
    items = [serialize_batch_item(item, resumes.get(item.resume_id)) for item in batch.items]
    items.sort(key=lambda i: (i["overall_score"] is None, -(i["overall_score"] or 0)))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["排名", "简历", "状态", "总分", "匹配分", "风险等级", "报告ID", "摘要"])
    for rank, item in enumerate(items, 1):
        writer.writerow(
            [
                rank,
                item["resume_title"],
                item["status"],
                item["overall_score"] if item["overall_score"] is not None else "",
                item["match_score"] if item["match_score"] is not None else "",
                RISK_LABEL.get(item["risk_level"] or "", ""),
                item["report_id"] or "",
                (item["summary"] or "").replace("\n", " "),
            ]
        )

    # 加 BOM，保证 Excel 直接打开不乱码
    content = "\ufeff" + buffer.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="zhenresume-batch-{batch_id}.csv"'},
    )
