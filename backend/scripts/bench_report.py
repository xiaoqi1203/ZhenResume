"""单份端到端耗时基准：对比串行 / 并行 / 不同模型下的真实性能。

用法（在 backend 目录下）：
    .\\.venv\\Scripts\\python.exe -m scripts.bench_report                     # 按当前 .env 配置跑一次
    .\\.venv\\Scripts\\python.exe -m scripts.bench_report --parallel true
    .\\.venv\\Scripts\\python.exe -m scripts.bench_report --model deepseek-v4-pro-0813
    .\\.venv\\Scripts\\python.exe -m scripts.bench_report --resume samples/sample_resume_1_weak.txt

它会真的建一个诊断任务、跑完整条 LangGraph 流水线，然后打印：
- 端到端耗时（task.created_at → completed_at）
- 每个节点的耗时 / 输出 token / 成本（直接读 workflow_steps）
- 串行关键路径 vs 并行后的差异

为什么要这个工具：`aaa.md` 的性能验收是"单份 ≤ 60 秒"，
但 mock 模式（1 秒）永远验证不出这条。改编排、换模型、压输出之后，
都用它来回答"到底快了多少"。只写数据库里的临时任务行，不碰知识库。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import AnalysisTask, JobDescription, Resume, WorkflowStep  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DEVICE = "bench-device"


def _make_task(resume_name: str, jd_name: str) -> str:
    resume_text = (SAMPLES / resume_name).read_text(encoding="utf-8")
    jd_text = (SAMPLES / jd_name).read_text(encoding="utf-8")

    from app.db import SessionLocal
    from app.models import User

    with session_scope() as db:
        user = db.execute(select(User).where(User.device_id == DEVICE)).scalar_one_or_none()
        if user is None:
            user = User(device_id=DEVICE, role="jobseeker", free_quota_used=0)
            db.add(user)
            db.flush()
        resume = Resume(user_id=user.id, title=f"[bench] {resume_name}", raw_text=resume_text,
                        mime_type="text/plain", size_bytes=len(resume_text.encode("utf-8")))
        jd = JobDescription(user_id=user.id, title="[bench] 后端开发工程师", raw_text=jd_text)
        db.add(resume)
        db.add(jd)
        db.flush()
        task = AnalysisTask(user_id=user.id, resume_id=resume.id, jd_id=jd.id, type="single",
                            status="pending", progress=0.0)
        db.add(task)
        db.flush()
        return task.id


def _report(task_id: str, elapsed: float, mode: str = "full") -> None:
    with session_scope() as db:
        rows = db.execute(
            select(WorkflowStep).where(WorkflowStep.task_id == task_id).order_by(WorkflowStep.created_at)
        ).scalars().all()
        steps = {r.step_name: r for r in rows}
        task = db.get(AnalysisTask, task_id)

    print("\n" + "=" * 68)
    print(f"端到端耗时：{elapsed:.1f}s　│　引擎={settings.workflow_engine}　图={mode}"
          f"　并行={settings.workflow_parallel}　checkpoint={settings.langgraph_checkpoint}")
    print(f"模型链：{' → '.join(settings.llm_chain_summary) or '(mock)'}")
    print("-" * 68)
    total_ms = 0
    for name, r in steps.items():
        ms = r.latency_ms or 0
        total_ms += ms
        print(f"  {name:<11} {ms:>7}ms  out={r.tokens:<6} ¥{(float(r.cost or 0)):.4f}  {r.status}")
    print("-" * 68)
    print(f"  {'各节点之和':<11} {total_ms:>7}ms（含并行重叠时，这个和会大于端到端耗时）")
    print(f"  状态={task.status}　progress={float(task.progress or 0)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="单份端到端耗时基准")
    parser.add_argument("--resume", default="sample_resume_1_weak.txt")
    parser.add_argument("--jd", default="sample_jd_backend.txt")
    parser.add_argument("--parallel", default="", help="true/false，覆盖 WORKFLOW_PARALLEL")
    parser.add_argument("--model", default="", help="覆盖主层模型，如 deepseek-v4-pro-0813")
    parser.add_argument("--max-tokens", type=int, default=0, help="覆盖 LLM_MAX_TOKENS")
    parser.add_argument("--screening", action="store_true", help="跑 HR 排序表轻量图（砍掉详细分析）")
    args = parser.parse_args()

    if args.parallel:
        settings.workflow_parallel = args.parallel.lower() == "true"
    if args.model:
        settings.aliyun_model = args.model
    if args.max_tokens:
        settings.llm_max_tokens = args.max_tokens

    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    task_id = _make_task(args.resume, args.jd)

    from app.workflow.graph import run_graph

    mode = "screening" if args.screening else "full"
    started = time.perf_counter()
    report_id = asyncio.run(run_graph(task_id, mode=mode))
    elapsed = time.perf_counter() - started

    _report(task_id, elapsed, mode)
    print(f"  报告：{report_id}")
    return 0 if report_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
