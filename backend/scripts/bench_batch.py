"""批量并发压测：合成 N 份简历，走**真实的 HR 批次并发路径**，报告吞吐 / 成本 / 失败率。

用法（在 backend 目录下）：

    .\\.venv\\Scripts\\python.exe -m scripts.bench_batch --count 10
    .\\.venv\\Scripts\\python.exe -m scripts.bench_batch --count 10 --concurrency 10
    .\\.venv\\Scripts\\python.exe -m scripts.bench_batch --count 10 --full   # 对照：完整流水线

设计要点：
- 直接调 `api.hr._process_batch(batch_id)`，**不复刻一遍并发逻辑** ——
  否则压测通过、线上挂掉这种事一定会发生。
- 简历与 JD 都按上传路径预跑规则解析并写入 `parsed_json`，与真实数据形状一致。
- 简历文本在 3 个样例间轮转，这样分数有高有低，能顺带验证"排序有区分度"。
- 只读/写业务表，不碰知识库。
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisReport, AnalysisTask, HrBatch, HrBatchItem, JobDescription, Resume, User, WorkflowStep,
)
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DEVICE = "bench-batch-device"


def _prepare(count: int) -> tuple[str, str]:
    """建一个批次（N 份简历 + 1 个 JD），返回 (batch_id, jd_id)。"""
    jd_text = (SAMPLES / "sample_jd_backend.txt").read_text(encoding="utf-8")
    texts = [p.read_text(encoding="utf-8") for p in sorted(SAMPLES.glob("sample_resume_*.txt"))]
    if not texts:
        raise SystemExit(f"没有样例简历：{SAMPLES}")

    with session_scope() as db:
        user = db.execute(select(User).where(User.device_id == DEVICE)).scalar_one_or_none()
        if user is None:
            user = User(device_id=DEVICE, role="hr", free_quota_used=0)
            db.add(user)
            db.flush()

        jd = JobDescription(
            user_id=user.id, title="[bench] 后端开发工程师", raw_text=jd_text,
            parsed_json=parse_jd_text(jd_text).model_dump(),
        )
        db.add(jd)
        db.flush()

        batch = HrBatch(user_id=user.id, jd_id=jd.id, status="pending", total=count, finished=0)
        db.add(batch)
        db.flush()

        for i in range(count):
            text = texts[i % len(texts)]
            resume = Resume(
                user_id=user.id, title=f"[bench] resume_{i + 1}.txt", raw_text=text,
                parsed_json=parse_resume_text(text).model_dump(),
                mime_type="text/plain", size_bytes=len(text.encode("utf-8")),
            )
            db.add(resume)
            db.flush()
            db.add(HrBatchItem(batch_id=batch.id, resume_id=resume.id, status="pending"))

        db.flush()
        return batch.id, jd.id


def _report(batch_id: str, elapsed: float, count: int) -> None:
    with session_scope() as db:
        batch = db.get(HrBatch, batch_id)
        items = list(batch.items) if batch else []
        statuses = Counter(i.status for i in items)
        scores = sorted([i.overall_score for i in items if i.overall_score is not None], reverse=True)

        # 每个候选人的任务墙钟时间（created_at → completed_at）
        walls: list[float] = []
        for item in items:
            if not item.task_id:
                continue
            t = db.get(AnalysisTask, item.task_id)
            if t and t.created_at and t.completed_at:
                walls.append((t.completed_at - t.created_at).total_seconds())

        task_ids = [i.task_id for i in items if i.task_id]
        steps = 0
        tokens = 0
        cost = 0.0
        per_step: dict[str, list[int]] = {}
        if task_ids:
            rows = db.execute(
                select(WorkflowStep).where(WorkflowStep.task_id.in_(task_ids))
            ).scalars().all()
            steps = len(rows)
            for r in rows:
                tokens += int(r.tokens or 0)
                cost += float(r.cost or 0)
                per_step.setdefault(r.step_name, []).append(int(r.latency_ms or 0))
            reports = db.execute(
                select(func.count()).select_from(AnalysisReport).where(AnalysisReport.task_id.in_(task_ids))
            ).scalar_one()
        else:
            reports = 0

    conc = max(1, settings.hr_batch_concurrency)
    waves = math.ceil(100 / conc)
    avg_wall = (sum(walls) / len(walls)) if walls else elapsed

    print("\n" + "=" * 70)
    print(f"压测结果：{count} 份　│　图={'完整流水线' if settings.workflow_engine and not settings.hr_screening else 'HR 排序表'}"
          f"　并发上限={conc}　引擎={settings.workflow_engine}　并行={settings.workflow_parallel}")
    print("-" * 70)
    print(f"  批次总墙钟        {elapsed:.1f}s")
    print(f"  条目状态          {dict(statuses)}　│ 报告数 {reports}/{count}")
    print(f"  分数分布          {scores}")
    print(f"  单份墙钟          avg {avg_wall:.1f}s / max {max(walls):.1f}s" if walls else "  单份墙钟          -")
    print(f"  有效吞吐          {count / elapsed:.2f} 份/秒 = {count / elapsed * 60:.1f} 份/分钟")
    print(f"  步骤行数          {steps}（HR 排序表应为 3/份，完整流水线 8/份）")
    print(f"  token 合计        {tokens}")
    print(f"  成本合计          ¥{cost:.4f}　│ 单份 ¥{cost / max(count, 1):.4f}")
    print("-" * 70)
    print(f"  外推 100 份：按当前并发 {conc} → {waves} 波 × 单份 {avg_wall:.1f}s ≈ {waves * avg_wall:.0f}s")
    if waves * avg_wall <= 60:
        print("  ✅ 预估满足「100 份 ≤ 60 秒」")
    else:
        need = math.ceil(100 * avg_wall / 60)
        print(f"  ⚠️ 预估超 60s；要达标需把并发提到 ≈ {need}（当前 {conc}），或把单份压到 {60 * conc / 100:.1f}s 以下")


def main() -> int:
    parser = argparse.ArgumentParser(description="HR 批次并发压测")
    parser.add_argument("--count", type=int, default=10, help="简历份数")
    parser.add_argument("--concurrency", type=int, default=0, help="覆盖 HR_BATCH_CONCURRENCY")
    parser.add_argument("--full", action="store_true", help="用完整流水线（对照），默认走 HR 排序表")
    args = parser.parse_args()

    if args.concurrency:
        settings.hr_batch_concurrency = args.concurrency
    if args.full:
        settings.hr_screening = False

    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    batch_id, _jd_id = _prepare(args.count)

    from app.api.hr import _process_batch

    started = time.perf_counter()
    asyncio.run(_process_batch(batch_id))
    elapsed = time.perf_counter() - started

    _report(batch_id, elapsed, args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
