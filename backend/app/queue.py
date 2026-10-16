"""任务投递：按 `WORKFLOW_EXECUTOR` 决定"谁来把工作流跑起来"。

- `inline`：FastAPI BackgroundTasks（进程内执行）。
  ⚠️ `scripts/verify.py` 依赖这个语义 —— "请求返回前后台任务已经跑完"。
- `celery`：投递到 Redis 队列，由**独立 worker 进程**执行。
  生产用它：web 进程不再被长任务占住，worker 可水平扩展，重启不丢任务。

两条路径最终都调用同一个 `workflow.run_analysis(task_id)`，业务代码零改动。
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks

from .config import settings

logger = logging.getLogger(__name__)


def enqueue_analysis(task_id: str, background_tasks: BackgroundTasks | None = None) -> str:
    """投递一份诊断任务（求职者完整流水线 / HR 排序表，由任务类型自动选图）。

    返回实际使用的执行器名，便于日志与测试断言。
    """
    if settings.workflow_executor == "celery":
        from .worker import run_analysis_task

        run_analysis_task.delay(task_id)
        logger.info("任务 %s 已投递到 Celery（broker=%s）", task_id, settings.celery_broker_url)
        return "celery"

    from .workflow import launch_workflow

    if background_tasks is None:  # 没有请求上下文（脚本/CLI）→ 直接同步跑
        launch_workflow(task_id)
        return "inline-sync"
    background_tasks.add_task(launch_workflow, task_id)
    return "inline"


def enqueue_batch(batch_id: str, background_tasks: BackgroundTasks | None = None) -> str:
    """投递一个 HR 批次。

    **整批一个任务**：候选人是网络等待型工作，一个进程内用 `asyncio` 并发 50 份，
    远比 50 个 prefork 进程省资源，也让 worker 并发数不必等于候选人数。
    """
    if settings.workflow_executor == "celery":
        from .worker import run_batch_task

        run_batch_task.delay(batch_id)
        logger.info("批次 %s 已投递到 Celery", batch_id)
        return "celery"

    from .api.hr import _run_batch

    if background_tasks is None:
        _run_batch(batch_id)
        return "inline-sync"
    background_tasks.add_task(_run_batch, batch_id)
    return "inline"
