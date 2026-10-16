"""workflow_steps 的幂等写入与 progress 推进。

为什么必须幂等：LangGraph 的 replay / resume 会**真的重跑节点**，
插入式写法会产生第二行 —— 于是 `步骤数为 8`、`SUM(tokens)`、`cost_per_report`
全部失真（"单份 ≤ 0.5 元"的看板会算错）。

并发安全：同一任务的多次写库用一把 asyncio.Lock 串行化，
并把阻塞式 DB 调用丢到线程里（`asyncio.to_thread`），不卡事件循环。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import session_scope
from ..models import AnalysisTask, WorkflowStep
from .state import CONTRACT_STEPS

_locks: dict[str, asyncio.Lock] = {}


def _lock_for(task_id: str) -> asyncio.Lock:
    lock = _locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[task_id] = lock
    return lock


async def record_step(task_id: str, step_name: str, **fields) -> None:
    """按 (task_id, step_name) upsert 一行，并推进 progress。"""
    async with _lock_for(task_id):
        await asyncio.to_thread(_record_step_sync, task_id, step_name, fields)


def _record_step_sync(task_id: str, step_name: str, fields: dict) -> None:
    with session_scope() as db:
        row = db.execute(
            select(WorkflowStep).where(
                WorkflowStep.task_id == task_id, WorkflowStep.step_name == step_name
            )
        ).scalar_one_or_none()
        if row is None:
            row = WorkflowStep(task_id=task_id, step_name=step_name, status="running")
            db.add(row)
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)
        db.flush()
        _advance_progress(db, task_id)


def _advance_progress(db, task_id: str) -> None:
    """progress = 已完成的契约步（去重）/ 8。重放安全，最终必为 1.0。"""
    rows = db.execute(select(WorkflowStep).where(WorkflowStep.task_id == task_id)).scalars().all()
    done = {r.step_name for r in rows if r.status in ("done", "degraded", "failed")}
    ordered = [name for name in CONTRACT_STEPS if name in done]
    task = db.get(AnalysisTask, task_id)
    if task is None:
        return
    task.status = "running"
    task.progress = round(len(ordered) / len(CONTRACT_STEPS), 4)
    if ordered:
        task.current_step = ordered[-1]


async def mark_task_running(task_id: str) -> None:
    await asyncio.to_thread(_mark_running_sync, task_id)


def _mark_running_sync(task_id: str) -> None:
    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is not None:
            task.status = "running"
            task.progress = 0.0
            task.error = None


def fail_task(task_id: str, message: str) -> None:
    """任务级失败（critical 节点异常、图异常终止）。同步函数，供任意上下文调用。"""
    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return
        task.status = "failed"
        task.error = message[:500]
        task.completed_at = datetime.now(timezone.utc)
