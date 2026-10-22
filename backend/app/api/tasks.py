"""分析任务接口（契约 §4）—— 工作流的入口、轮询出口与 SSE 流。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import JdNotFound, QuotaExceeded, ResumeNotFound, TaskNotFound, ValidationError
from ..models import AnalysisReport, AnalysisTask, JobDescription, Payment, Resume, User
from ..schemas import TaskCreate
from ..queue import enqueue_analysis
from ..serializers import serialize_task, serialize_task_summary
from ..workflow import launch_workflow
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

STREAM_POLL_S = 0.5          # 服务端轮询间隔
STREAM_HEARTBEAT_POLLS = 20  # 每 20 次（~10s）发一个心跳注释行，防代理断连
STREAM_MAX_S = 900           # 硬上限，避免连接泄漏

def _has_paid_access(db: Session, user: User) -> bool:
    """是否存在有效付费记录（MVP 只判断有没有成功支付过）。"""
    paid = db.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == user.id, Payment.status == "paid")
    ).scalar_one()
    return bool(paid)


# 少于这个字符数视为"没抽取到内容"：正常简历的姓名+联系方式就超过它了
MIN_RESUME_CHARS = 40


def analyze_guard(resume: Resume) -> str | None:
    """判断这份简历是否值得分析；不值得返回**给用户看的原因**，值得返回 None。

    判据（都来自实测出的失败样本）：
    - `raw_text` 几乎为空（空文件、扫描件 OCR 失败、纯空白）；
    - 解析结果里既没有工作经历、也没有项目/教育/技能 —— 这类输入跑完整流水线
      只会得到"中等水平、风险较低"的假结论。

    只在**任务创建时**拦截，不拦上传：上传件仍可下载/导出，用户能看到自己传了什么。
    """
    raw = (resume.raw_text or "").strip()
    if len(raw) < MIN_RESUME_CHARS:
        return (
            "这份简历没有抽取到可分析的文本（可能是扫描件、图片或空文件）。"
            "请上传含文字层的 PDF / DOCX，或直接粘贴纯文本后再诊断。"
        )
    parsed = resume.parsed_json or {}
    has_structure = any(
        parsed.get(key)
        for key in ("work", "projects", "education", "skills")
    )
    if not has_structure:
        return (
            "简历文本已抽取，但没有识别出工作经历、项目、教育或技能中的任何一项，"
            "继续诊断会得到没有依据的分数。请检查排版（多栏版的建议改用单栏模板）后重新上传。"
        )
    return None


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

    # ---- 可分析性闸门 ----
    # 没有这一步，"扫描件 OCR 失败 → raw_text 为空"会被一路跑完，产出一份
    # 「综合得分 55 分、风险较低」的报告（实测：空文本 / 纯空白 / 纯英文 /
    # 全角乱码共 8 个边界输入都是 green）。给用户一个假结论比报错更伤信任。
    unanalyzable = analyze_guard(resume)
    if unanalyzable:
        raise ValidationError(
            unanalyzable,
            code="RESUME_UNANALYZABLE",
        )

    # 免费额度闸门（默认关闭；QUOTA_ENFORCED=true 时才生效）
    if settings.quota_enforced and user.free_quota_used >= settings.free_quota and not _has_paid_access(db, user):
        raise QuotaExceeded(f"免费额度 {settings.free_quota} 次已用完，请选择付费套餐继续诊断")

    task = AnalysisTask(
        user_id=user.id,
        resume_id=resume.id,
        jd_id=jd.id,
        type="single",
        mode=payload.mode,
        status="pending",
        progress=0.0,
    )
    db.add(task)
    user.free_quota_used = (user.free_quota_used or 0) + 1
    db.commit()
    db.refresh(task)

    # 异步执行：Starlette 会 await 这个 async 函数，请求立即返回 202。
    # 具体走 BackgroundTasks 还是 Celery，由 WORKFLOW_EXECUTOR 决定（见 app/queue.py）
    enqueue_analysis(task.id, background_tasks)

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

    report = db.execute(select(AnalysisReport).where(AnalysisReport.task_id == task.id)).scalar_one_or_none()
    return serialize_task(
        task,
        steps=list(task.steps),
        report_id=report.id if report else None,
        report_extra=report.extra_json if report else None,
    )


# --------------------------------------------------------------------------- #
# 契约 §4.4：SSE 流式任务进度
# --------------------------------------------------------------------------- #
def _sse(event: str, data: dict) -> str:
    """SSE 帧。data 必须是单行 JSON —— 多行会被 SSE 规范拆坏。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _snapshot(task_id: str) -> dict | None:
    """读一次任务快照（在**独立线程**里用独立 session，绝不跨 await 持会话）。

    Celery 模式下任务跑在别的进程，所以只能用 DB 状态作为唯一真源。
    """
    from ..db import session_scope

    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return None
        report = db.execute(
            select(AnalysisReport).where(AnalysisReport.task_id == task.id)
        ).scalar_one_or_none()
        return serialize_task(
            task,
            steps=list(task.steps),
            report_id=report.id if report else None,
            report_extra=report.extra_json if report else None,
        )


async def _event_stream(task_id: str) -> AsyncIterator[str]:
    """把任务状态变化翻译成 SSE 事件。

    事件模型（契约 §4.4）：
      step_done   某个步骤从 pending → done/degraded（前端可立即渲染这一段结论）
      progress    进度/当前步骤/状态发生变化
      done        任务完成，data 是完整任务详情（与 4.2 同构）→ 服务端随后关闭
      error       任务失败
      : ping      心跳注释行（不产生事件，只为防代理/浏览器断连）
    """
    started = time.monotonic()
    sent_steps: set[str] = set()
    last_fingerprint: tuple | None = None
    polls = 0

    while True:
        if time.monotonic() - started > STREAM_MAX_S:
            yield _sse("error", {"message": f"流式连接超过 {STREAM_MAX_S}s 上限，已关闭；请用 4.2 轮询续取"})
            return

        snapshot = await asyncio.to_thread(_snapshot, task_id)
        if snapshot is None:
            yield _sse("error", {"message": "任务不存在"})
            return

        # 1) 已完成的步骤逐个推送（不重复推）
        for step in snapshot.get("steps") or []:
            name = step.get("step_name")
            if name in sent_steps or step.get("status") == "pending":
                continue
            sent_steps.add(name)
            yield _sse("step_done", step)

        # 2) 进度变化
        fingerprint = (
            snapshot.get("status"),
            snapshot.get("current_step"),
            round(float(snapshot.get("progress") or 0), 3),
        )
        if fingerprint != last_fingerprint:
            last_fingerprint = fingerprint
            yield _sse(
                "progress",
                {
                    "task_id": task_id,
                    "status": snapshot.get("status"),
                    "progress": snapshot.get("progress"),
                    "current_step": snapshot.get("current_step"),
                    "total_steps": snapshot.get("total_steps"),
                },
            )

        # 3) 终态：把完整结果一并给出，然后关闭（前端不必再调 4.2）
        status = snapshot.get("status")
        if status == "done":
            yield _sse("done", snapshot)
            return
        if status == "failed":
            yield _sse("error", {"message": snapshot.get("error") or "任务失败", "task_id": task_id})
            return

        polls += 1
        if polls % STREAM_HEARTBEAT_POLLS == 0:
            yield ": ping\n\n"
        await asyncio.sleep(STREAM_POLL_S)


@router.get("/{task_id}/stream")
def stream_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE 流式任务进度（契约 §4.4）。

    ⚠️ 客户端**必须用 `fetch()` + ReadableStream 读取，不能用 `EventSource`**：
    身份靠请求头（`X-Device-Id`），而 `EventSource` 不支持自定义请求头。
    这也是为什么这里不做 `?device_id=` 的查询参数兜底 ——
    凭证出现在 URL 里会被写进访问日志，属于无谓的泄漏面。

    保留 §4.2 轮询接口不变：老客户端与"断线续取"都继续可用。
    """
    task = db.get(AnalysisTask, task_id)
    if task is None or task.user_id != user.id:
        raise TaskNotFound()
    return StreamingResponse(
        _event_stream(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx 默认会缓冲响应，必须显式关掉，否则前端收不到增量（只在整条流结束时才收到）
            "X-Accel-Buffering": "no",
        },
    )
