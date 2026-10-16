"""Celery worker：把工作流执行从 web 进程搬出去。

在**虚拟机**上启动（backend 目录下）：

```bash
./.venv/bin/celery -A app.worker worker -l INFO -Q default --concurrency=4
```

为什么并发数要小（默认 4 而不是 50）：
每个 Celery 任务是"一份诊断**或整个 HR 批次**"，批次任务内部自己用
`asyncio.gather` + 信号量并发跑候选人。所以 worker 并发 4 就足够，
真正限制吞吐的是 `HR_BATCH_CONCURRENCY` 与 LLM 配额，不是 worker 进程数。

配置取舍：
- `task_acks_late=True`：worker 崩了任务会回到队列重跑（配合幂等的 step upsert / 报告 upsert，
  重跑不会产生重复行 —— 这是 LangGraph 改造时特意做的）。
- `worker_prefetch_multiplier=1`：长任务公平调度，否则一个 worker 会囤一堆任务慢慢跑。
- `task_time_limit`：硬超时，防止一个卡死的 LLM 调用永久占用 worker 槽。
"""

from __future__ import annotations

import asyncio
import logging

from celery import Celery
from celery.signals import worker_ready

from .config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "zhenresume",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    # ⚠️ **必须显式指定队列名**：Celery 默认把任务发到 `celery` 队列，
    # 而 worker 常用 `-Q default` 启动 —— 两边不一致时任务会永远积压在队列里没人消费
    # （实测踩过：worker 显示 ready、任务不执行、redis 里 celery 队列长度一直涨）。
    task_default_queue="default",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=settings.celery_task_time_limit_s,
    task_soft_time_limit=settings.celery_task_soft_time_limit_s,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    enable_utc=True,
    timezone="Asia/Shanghai",
)


@celery_app.task(name="zhenresume.run_analysis", bind=True, max_retries=0)
def run_analysis_task(self, task_id: str) -> str | None:  # noqa: ARG001
    """一份诊断：完整 8 节点流水线（求职者）或 HR 排序表（批量），按任务类型自动选图。"""
    from .workflow import run_analysis

    return asyncio.run(run_analysis(task_id))


@celery_app.task(name="zhenresume.run_batch", bind=True, max_retries=0)
def run_batch_task(self, batch_id: str) -> None:  # noqa: ARG001
    """一个 HR 批次：内部用 asyncio 并发跑全部候选人。"""
    from .api.hr import _process_batch

    asyncio.run(_process_batch(batch_id))


@worker_ready.connect
def _on_worker_ready(**_kwargs) -> None:
    logger.info(
        "Celery worker 就绪：broker=%s backend=%s 图=%s 批次并发=%s",
        settings.celery_broker_url,
        settings.celery_result_backend,
        "HR 排序表" if settings.hr_screening else "完整流水线",
        settings.hr_batch_concurrency,
    )
