"""HR 批量初筛接口（契约 §6.5，对应 aaa.md §3.9）。

实现方式：每份简历创建一个独立 AnalysisTask 并复用同一套 8-Agent 工作流，
这样单份报告与批量报告的质量、字段、可追溯性完全一致，不会出现"批量版缩水"。

**MVP 已知简化（诚实标注）**：
- 批量任务不做并发控制，串行执行；100 份简历会跑较久，生产环境应换成 Redis + Celery 并发。
  （已解决：现在批次内用 asyncio + 信号量并发，见 `_process_batch`）
- 批量不消耗免费额度也不校验 HR 套餐配额，配额逻辑留在 tasks 接口。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import traceback
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, session_scope
from ..errors import BatchNotFound, JdNotFound, ResumeNotFound, ValidationError
from ..models import AnalysisReport, AnalysisTask, HrBatch, HrBatchItem, JobDescription, Resume, User
from ..queue import enqueue_batch
from ..schemas import BatchCreate
from ..serializers import serialize_batch, serialize_batch_item
from ..services import dedup
from ..workflow import run_analysis
from .deps import get_current_user

router = APIRouter(prefix="/hr", tags=["hr"])

RISK_LABEL = {"red": "高风险", "yellow": "需关注", "green": "良好"}

# ---- SSE 参数（与 tasks 的 §4.4 保持一致）----
STREAM_POLL_S = 0.5
STREAM_HEARTBEAT_POLLS = 20
STREAM_MAX_S = 3600  # 批次可能跑几十分钟，上限比单份宽


def _sse(event: str, data: dict) -> str:
    """SSE 帧（与 tasks.py 的实现一致：data 必须是单行 JSON）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_batch(batch_id: str) -> None:
    try:
        asyncio.run(_process_batch(batch_id))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


async def _process_batch(batch_id: str) -> None:
    """并发跑整个批次。

    为什么可以并发：每个候选人是一个**完全独立**的任务（独立 thread、独立简历与 JD），
    互相不共享状态；LLM 调用是网络等待，串行跑纯属浪费。
    用信号量把"同时在跑的份数"限住（`HR_BATCH_CONCURRENCY`），
    这样在飞的 LLM 请求数 ≈ 并发份数 × 每份并行度（HR 排序表图每份最多 2 个），
    不会一下子把供应商配额打满。

    注意：仍然在 `BackgroundTasks` 的 `asyncio.run` 里跑完再返回 ——
    `scripts/verify.py` 依赖"请求返回前工作流已跑完"这个语义，别在这里改成队列。
    """
    with session_scope() as db:
        batch = db.get(HrBatch, batch_id)
        if batch is None:
            return
        batch.status = "running"
        item_ids = [item.id for item in batch.items]
        jd_id = batch.jd_id
        user_id = batch.user_id
        db.flush()

    semaphore = asyncio.Semaphore(max(1, settings.hr_batch_concurrency))

    async def one(item_id: str) -> None:
        async with semaphore:
            await _process_item(item_id, batch_id, jd_id, user_id)

    # return_exceptions=True：单个候选人失败不影响整批（原来一层 try 包整批，语义更差）
    results = await asyncio.gather(*(one(i) for i in item_ids), return_exceptions=True)
    for item_id, result in zip(item_ids, results):
        if isinstance(result, BaseException):
            traceback.print_exception(type(result), result, result.__traceback__)
            with session_scope() as db:
                item = db.get(HrBatchItem, item_id)
                if item is not None and item.status != "done":
                    item.status = "failed"
                batch = db.get(HrBatch, batch_id)
                if batch is not None:
                    batch.finished = (batch.finished or 0) + 1

    with session_scope() as db:
        batch = db.get(HrBatch, batch_id)
        if batch is not None:
            batch.status = "done"


async def _process_item(item_id: str, batch_id: str, jd_id: str | None, user_id: str | None) -> None:
    """单个候选人：建任务 → 跑工作流 → 回写分数。事务按段独立，不跨 await 持有会话。"""
    with session_scope() as db:
        item = db.get(HrBatchItem, item_id)
        if item is None:
            return

        # ---- 去重命中：同一份简历 + **同一个 JD** 在 7 天内有报告 → 直接复用，不重复花钱 ----
        # 必须匹配 jd_id：报告是"这份简历 vs 那个 JD"的结论，换 JD 复用就是错的。
        reused = dedup.recent_report_id(db, item.resume_id, jd_id, within_days=7)
        if reused:
            report = db.get(AnalysisReport, reused)
            item.status = "done"
            item.report_id = reused
            if report is not None:
                item.overall_score = report.overall_score
                item.match_score = report.match_score
                item.risk_level = report.risk_level
                item.summary = report.summary
            batch = db.get(HrBatch, batch_id)
            if batch is not None:
                batch.finished = (batch.finished or 0) + 1
            db.flush()
            return

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

    report_id = await run_analysis(task_id)

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
    seen: set[str] = set()
    for resume_id in payload.resume_ids:
        # 批次内去重（只去重、不标记）：
        # 上传层已经做了 L1/L2 去重 —— 同一份简历重复上传会返回**同一个 resume_id**，
        # 所以这里一定会出现同一个 id 传两次的情况。不去重的话一个人会占两个名次，
        # HR 看到的排序就是假的（这正是去重要解决的问题之一）。
        if resume_id in seen:
            continue
        seen.add(resume_id)
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

    enqueue_batch(batch.id, background_tasks)

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


# --------------------------------------------------------------------------- #
# 契约 §6.5：批次进度流（SSE）
# --------------------------------------------------------------------------- #
def _batch_snapshot(batch_id: str) -> dict | None:
    """读一次批次快照（在**独立线程**里用独立 session，不跨 await 持会话）。

    为什么服务端靠轮询 DB 而不是进程内事件总线：批次可能跑在 **Celery worker**
    （另一个进程/容器）里，进程内事件根本拿不到；`hr_batch_items` 才是唯一真源。
    """
    with session_scope() as db:
        batch = db.get(HrBatch, batch_id)
        if batch is None:
            return None
        resumes = {
            r.id: r
            for r in db.execute(select(Resume).where(Resume.id.in_([i.resume_id for i in batch.items]))).scalars().all()
        }
        items = [serialize_batch_item(item, resumes.get(item.resume_id)) for item in batch.items]
        items.sort(key=lambda i: (i["overall_score"] is None, -(i["overall_score"] or 0)))
        return serialize_batch(batch, items=items)


async def _batch_event_stream(batch_id: str) -> AsyncIterator[str]:
    """事件模型（契约 §6.5）：

    - `item_done`  某个候选人出结果（前端可立刻在列表里点亮一行，这是价值所在）
    - `progress`   `finished/total` 变化
    - `done`       整批完成，data 与 §6.5 详情接口同构 → 服务端随后关闭
    - `error`      批次不存在 / 超过流上限
    - `: ping`     约 10s 一次心跳，防代理断连
    """
    started = time.monotonic()
    sent: set[str] = set()
    last_progress: tuple | None = None
    polls = 0

    while True:
        if time.monotonic() - started > STREAM_MAX_S:
            yield _sse("error", {"message": f"流式连接超过 {STREAM_MAX_S}s 上限，已关闭；请用 §6.5 详情接口续取"})
            return

        snapshot = await asyncio.to_thread(_batch_snapshot, batch_id)
        if snapshot is None:
            yield _sse("error", {"message": "批次不存在"})
            return

        # 已出结果的候选人逐个推送（只推一次）。用 report_id 去重：
        # 同一份简历在批次里出现两次时也是两个独立任务/两份报告，不会互相顶掉。
        for item in snapshot.get("items") or []:
            if item.get("status") not in {"done", "failed"}:
                continue
            key = str(item.get("report_id") or item.get("resume_id") or "")
            if not key or key in sent:
                continue
            sent.add(key)
            yield _sse("item_done", item)

        progress = (snapshot.get("status"), snapshot.get("finished"), snapshot.get("total"))
        if progress != last_progress:
            last_progress = progress
            yield _sse(
                "progress",
                {
                    "batch_id": batch_id,
                    "status": snapshot.get("status"),
                    "finished": snapshot.get("finished"),
                    "total": snapshot.get("total"),
                },
            )

        status = snapshot.get("status")
        if status == "done":
            yield _sse("done", snapshot)
            return
        if status == "failed":
            yield _sse("error", {"message": "批次执行失败", "batch_id": batch_id})
            return

        polls += 1
        if polls % STREAM_HEARTBEAT_POLLS == 0:
            yield ": ping\n\n"
        await asyncio.sleep(STREAM_POLL_S)


@router.get("/batches/{batch_id}/stream")
def stream_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """批次进度流（契约 §6.5）。

    与 §4.4 同样的约束：客户端必须用 `fetch()` + ReadableStream 读取
    （要带 `X-Device-Id` 请求头，而 `EventSource` 不支持自定义头）。
    §6.5 的详情接口保留 —— 断线续取与老客户端继续可用。
    """
    batch = db.get(HrBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise BatchNotFound()
    return StreamingResponse(
        _batch_event_stream(batch_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx 默认缓冲响应，不加这个前端要等整条流结束才收到内容
            "X-Accel-Buffering": "no",
        },
    )


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
