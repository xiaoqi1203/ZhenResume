"""端到端验收：单份诊断在 `WORKFLOW_MODE=agentic` 下跑通，并检查引用链路。

验收点：
1. 图里确实有 `agentic_evidence` 节点，且 `workflow_steps` 仍是契约的 8 行（不增不减）；
2. 报告 `extra_json.agent_trace` 有检索轨迹（query 序列、轮次、校验统计）；
3. 最终 findings 上的 `citations` 全部通过 `verify_citations`（chunk 存在 + quote 逐字可定位）；
4. 分数与 `fixed` 模式同口径（都是确定性评分算出来的）。

用法（在 backend 目录下）：
    python -m scripts.smoke_agentic_report            # 用本地库里第一条 简历×JD
    python -m scripts.smoke_agentic_report --keep     # 保留任务
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 必须在 import app.config 之前设置
os.environ.setdefault("WORKFLOW_ENGINE", "langgraph")
os.environ.setdefault("WORKFLOW_EXECUTOR", "inline")
os.environ["WORKFLOW_MODE"] = "agentic"

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import AnalysisReport, AnalysisTask, JobDescription, Resume, WorkflowStep  # noqa: E402
from app.services.citation import verify_citations  # noqa: E402
from app.workflow import run_analysis  # noqa: E402
from app.workflow.state import CONTRACT_STEPS  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print(f"WORKFLOW_MODE={settings.workflow_mode}｜ENGINE={settings.workflow_engine}｜EXECUTOR={settings.workflow_executor}")
    assert settings.workflow_mode == "agentic"

    with session_scope() as db:
        resume = db.execute(select(Resume).limit(1)).scalar_one_or_none()
        jd = db.execute(select(JobDescription).limit(1)).scalar_one_or_none()
        if resume is None or jd is None:
            print("✗ 本地库里没有简历/JD，先上传或跑 scripts/seed_demo.py")
            return 1
        task = AnalysisTask(
            user_id=resume.user_id, resume_id=resume.id, jd_id=jd.id,
            # 必须显式写 agentic：`AnalysisTask.mode` 默认 "fixed"，而
            # `engine._task_workflow_mode()` 会**按任务**覆盖全局 WORKFLOW_MODE。
            # 不写这一行，本脚本即使设了环境变量也会跑成 fixed 图（没有 agentic_evidence
            # 节点 → agent_trace 为空 → 本脚本的验收断言必然失败）。
            type="single", mode="agentic", status="pending", progress=0.0,
        )
        db.add(task)
        db.flush()
        task_id = task.id
        print(f"任务 {task_id}｜简历 {resume.title or resume.id[:8]}｜JD {jd.title}")

    t0 = time.perf_counter()
    await run_analysis(task_id)
    elapsed = time.perf_counter() - t0
    print(f"工作流耗时 {elapsed:.1f}s")

    with session_scope() as db:
        task = db.get(AnalysisTask, task_id)
        report = db.execute(select(AnalysisReport).where(AnalysisReport.task_id == task_id)).scalar_one_or_none()
        print(f"任务状态={task.status}｜progress={task.progress}")
        if report is None:
            print("✗ 未产出报告")
            return 1
        trace = (report.extra_json or {}).get("agent_trace") or {}
        steps = db.execute(
            select(WorkflowStep).where(WorkflowStep.task_id == task_id)
        ).scalars().all()
        step_names = [s.step_name for s in steps]
        findings = list(report.findings)
        raw_citations = []
        for f in findings:
            ev = f.evidence_ref or {}
            raw_citations.extend(ev.get("citations") or [])
        # 这里是从库里读**已落库**的 citations 做复核，不传 retrieved_chunk_ids：
        # - 这份数组里可能混有分析阶段（source="analyzer"）的引用，它们来自 fixed 检索，
        #   不属于本次 agentic 的 retrieval_hits，硬塞集合会把合法引用误判成"未检索到"；
        # - 保持 None 即维持本脚本原有语义（chunk 存在 + quote 可定位）。
        verified, vstats = verify_citations(db, raw_citations)
        scores = {
            "overall": report.overall_score, "ats": report.ats_score, "impact": report.impact_score,
            "clarity": report.clarity_score, "match": report.match_score, "risk": report.risk_score,
        }

    print(f"\n契约步数 {len(step_names)}（应为 8）：{step_names}")
    print(f"是否有 agentic_evidence 步: {'agentic_evidence' in step_names}（应为 False，非契约步不进 workflow_steps）")
    print(f"\n分数（确定性）: {scores}")
    print(f"\nAgent 轨迹: status={trace.get('status')} 模型往返={trace.get('model_calls')} "
          f"工具={trace.get('tool_calls')} 检索(请求/执行/拦截)={trace.get('retrieval_requests')}/{trace.get('retrieval_rounds')}/{trace.get('retrieval_blocked')} "
          f"tokens={trace.get('tokens')} 成本=¥{trace.get('cost')} 延迟={trace.get('latency_ms')}ms")
    for i, q in enumerate(trace.get("retrieval_queries") or [], 1):
        print(f"  {i}. {q}")
    if trace.get("verify"):
        print(f"  校验: {trace['verify']}")
    if trace.get("attach"):
        print(f"  挂载: {trace['attach']}")
    if trace.get("reasoning"):
        print(f"  自述: {str(trace['reasoning'])[:240]}")
    print(f"\n结论级引用: 报告 findings 共 {len(findings)} 条，带 citations 的 "
          f"{len([f for f in findings if (f.evidence_ref or {}).get('citations')])} 条")
    print(f"  服务端复核: 引用 {vstats['total']} 条｜通过 {vstats['verified']}｜"
          f"chunk 不存在 {vstats['rejected_missing_chunk']}｜quote 定位失败 {vstats['rejected_quote_not_found']}｜"
          f"未检索到 {vstats['rejected_not_retrieved']}（此处不传本次检索集合，恒为 0）")

    ok = (
        len(step_names) == len(CONTRACT_STEPS)
        and "agentic_evidence" not in step_names
        and bool(trace.get("retrieval_rounds"))
        and vstats["total"] > 0
        and vstats["verified"] == vstats["total"]
    )
    print("\n结论:", "✓ agentic 模式端到端通过（8 步契约不变 + 多轮检索 + 引用全通过）" if ok else "✗ 存在未达标项")

    if not args.keep:
        with session_scope() as db:
            db.query(AnalysisReport).filter(AnalysisReport.task_id == task_id).delete(synchronize_session=False)
            db.query(AnalysisTask).filter(AnalysisTask.id == task_id).delete(synchronize_session=False)
        print("（已清理本次任务与报告）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
