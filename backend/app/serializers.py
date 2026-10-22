"""序列化器：把 ORM 对象转成 docs/api-contract.md 里逐字定义的响应结构。

刻意不用 FastAPI 的 response_model：报告结构较深，显式构造 dict 更容易保证
字段名/枚举值与契约一致，也避免 ORM 关系触发额外查询。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .agents.report_agent import DISCLAIMER
from .models import AnalysisReport, AnalysisTask, HrBatch, HrBatchItem, JobDescription, KnowledgeDoc, Resume, WorkflowStep
from .schemas import ParsedJd, ParsedResume


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_resume(raw: dict | None) -> dict:
    try:
        return ParsedResume.model_validate(raw or {}).model_dump()
    except Exception:  # noqa: BLE001
        return ParsedResume.empty().model_dump()


def _safe_jd(raw: dict | None) -> dict:
    try:
        return ParsedJd.model_validate(raw or {}).model_dump()
    except Exception:  # noqa: BLE001
        return ParsedJd.empty().model_dump()


def _editor_content_of(resume: Resume | None) -> dict | None:
    """取出编辑器原文（契约 §2.3：存在 parsed_json.editor_content 里）。

    它同时充当「这份简历是否由编辑器创建」的判据：上传的简历没有这个键。
    """
    if resume is None:
        return None
    raw = resume.parsed_json or {}
    stored = raw.get("editor_content")
    return stored if isinstance(stored, dict) else None


def _resume_source(resume: Resume | None) -> str:
    return "editor" if _editor_content_of(resume) else "upload"


def _template_code_of(resume: Resume | None) -> str | None:
    """简历选用的模板 code（存在 `parsed_json["template_code"]`，不是独立列）。

    和 `editor_content` 同样的思路：pydantic 默认忽略未知键，所以
    `ParsedResume.model_validate` 读到它不会报错，MVP 阶段不必引入数据库迁移。
    上传的简历 / 未选模板的简历没有这个键，统一返回 None。
    """
    if resume is None:
        return None
    raw = resume.parsed_json or {}
    code = raw.get("template_code")
    if isinstance(code, str):
        code = code.strip()
        return code or None
    return None


# --------------------------------------------------------------------------- #
# 简历 / JD
# --------------------------------------------------------------------------- #
def serialize_resume(resume: Resume, *, with_text: bool = True) -> dict:
    payload = {
        "resume_id": resume.id,
        "title": resume.title or "",
        "file_url": resume.file_url or "",
        "parsed": _safe_resume(resume.parsed_json),
        "source": _resume_source(resume),
        # 模板 code：前端据此回填模板选择；上传的简历 / 未选模板为 None
        "template_code": _template_code_of(resume),
        "editor_content": _editor_content_of(resume),
        "created_at": iso(resume.created_at),
    }
    if with_text:
        payload["raw_text"] = resume.raw_text or ""
    return payload


def serialize_resume_summary(resume: Resume) -> dict:
    parsed = _safe_resume(resume.parsed_json)
    basics = parsed.get("basics") or {}
    work = parsed.get("work") or []
    return {
        "resume_id": resume.id,
        "title": resume.title or "",
        "created_at": iso(resume.created_at),
        "parsed_name": basics.get("name") or "",
        "parsed_title": (work[0].get("title") if work else "") or "",
        "source": _resume_source(resume),
        "template_code": _template_code_of(resume),
    }


def serialize_jd(jd: JobDescription, *, with_text: bool = True) -> dict:
    payload = {
        "jd_id": jd.id,
        "title": jd.title or "",
        "company": jd.company or "",
        "parsed": _safe_jd(jd.parsed_json),
        "created_at": iso(jd.created_at),
    }
    if with_text:
        payload["raw_text"] = jd.raw_text or ""
    return payload


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
def serialize_step(step: WorkflowStep) -> dict:
    return {
        "step_name": step.step_name or "",
        "status": step.status or "pending",
        "latency_ms": step.latency_ms,
        "tokens": step.tokens,
        "cost": float(step.cost) if step.cost is not None else None,
    }


STEP_ORDER = ["parse", "jd_match", "ats", "impact", "risk", "suggestion", "report", "qc"]


def serialize_task(task: AnalysisTask, *, steps: list[WorkflowStep] | None = None, report_id: str | None = None, report_extra: dict | None = None) -> dict:
    """steps 未落库的步骤补成 pending，保证前端时间线始终有 8 行。

    `usage` 是**给用户看的账单**，不是内部 metric：
    - `latency_ms`：端到端耗时（任务创建 → 完成），不是 8 步相加 ——
      因为并行（ats/impact/risk 三路）与证据节点都不在该求和中，相加会明显少算；
    - `tokens` / `cost`：8 个契约步 + 证据 Agent 节点的总和。
      ⚠️ 证据节点**不写 workflow_steps**（非契约节点），它的消耗在
      `report.extra_json.agent_trace` 里。不把这部分算进去，用户看到的花费会比实际少
      一大截（实测证据节点一次 ¥0.13，比 8 步总和还贵）。
    - 未完成时也给出**已发生**的消耗，前端可以实时显示，而不是完成才看得到。
    """
    by_name = {s.step_name: s for s in (steps or [])}
    ordered = [
        serialize_step(by_name[name])
        if name in by_name
        else {"step_name": name, "status": "pending", "latency_ms": None, "tokens": None, "cost": None}
        for name in STEP_ORDER
    ]

    step_tokens = sum(int(s.get("tokens") or 0) for s in ordered)
    step_cost = sum(float(s.get("cost") or 0) for s in ordered)
    step_latency = sum(int(s.get("latency_ms") or 0) for s in ordered)

    trace = (report_extra or {}).get("agent_trace") or {}
    trace_tokens = int(trace.get("tokens") or 0) if isinstance(trace, dict) else 0
    trace_cost = float(trace.get("cost") or 0) if isinstance(trace, dict) else 0
    trace_latency = int(trace.get("latency_ms") or 0) if isinstance(trace, dict) else 0

    finished = serialize_iso(task.completed_at) if task.completed_at else None
    return {
        "task_id": task.id,
        "status": task.status or "pending",
        "mode": getattr(task, "mode", None) or "fixed",
        "progress": float(task.progress or 0),
        "current_step": task.current_step,
        "total_steps": len(STEP_ORDER),
        "steps": ordered,
        "report_id": report_id,
        "error": task.error,
        "created_at": serialize_iso(task.created_at),
        "completed_at": finished,
        "usage": {
            # 端到端墙钟：完成用 completed_at-created_at，未完成则统计各步之和作为下限
            "latency_ms": _elapsed_ms(task, step_latency),
            "tokens": step_tokens + trace_tokens,
            "cost": round(step_cost + trace_cost, 4),
            # 拆开展示，让用户知道"贵在哪"（证据节点通常是大头）
            "steps_cost": round(step_cost, 4),
            "evidence_cost": round(trace_cost, 4),
            "evidence_tokens": trace_tokens,
            "evidence_latency_ms": trace_latency,
        },
    }


def _elapsed_ms(task: AnalysisTask, fallback_ms: int) -> int:
    """端到端耗时（毫秒）。

    完成的任务用 `completed_at - created_at`（准确）；
    运行中的任务两者都没有完成时间，用各步 latency 之和 —— 明知道会比真实值小
    （并行步骤重叠），但它是"已确认发生的耗时"下界，比显示 0 更有信息量。
    """
    if task.completed_at and task.created_at:
        delta = task.completed_at - task.created_at
        return max(0, int(delta.total_seconds() * 1000))
    return max(0, int(fallback_ms))


def serialize_iso(value: datetime | None) -> str | None:
    """`iso()` 的别名，语义更清楚（task 序列化里两处都用）。"""
    return iso(value)


def serialize_task_summary(task: AnalysisTask, report_id: str | None = None) -> dict:
    return {
        "task_id": task.id,
        "resume_id": task.resume_id,
        "jd_id": task.jd_id,
        "status": task.status or "pending",
        "created_at": iso(task.created_at),
        "report_id": report_id,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _evidence_only(evidence_ref: dict | None) -> dict:
    """evidence 字段保持既有形状：把落库时混存的 citations 剥掉。"""
    from .services.citation import split_evidence_and_citations

    ev, _ = split_evidence_and_citations(evidence_ref)
    return ev or {"source": "resume", "quote": "", "span": None, "field": None}


def _citations_of(evidence_ref: dict | None) -> list[dict]:
    from .services.citation import split_evidence_and_citations

    _, citations = split_evidence_and_citations(evidence_ref)
    return citations


def serialize_finding(finding) -> dict:
    return {
        "finding_id": finding.id,
        "category": finding.category or "ats",
        "severity": finding.severity or "yellow",
        "title": finding.title or "",
        "original_text": finding.original_text or "",
        "issue": finding.issue or "",
        "evidence": _evidence_only(finding.evidence_ref),
        "citations": _citations_of(finding.evidence_ref),
        "suggestion": finding.suggestion or "",
        "rewritten_text": finding.rewritten_text or "",
    }


def serialize_report(
    report: AnalysisReport,
    *,
    task: AnalysisTask | None = None,
    resume: Resume | None = None,
    jd: JobDescription | None = None,
    benchmark: dict | None = None,
) -> dict:
    extra = report.extra_json or {}
    editor_content = _editor_content_of(resume)
    return {
        "report_id": report.id,
        "task_id": report.task_id,
        "resume_id": resume.id if resume else (task.resume_id if task else None),
        "resume_title": (resume.title if resume else "") or "",
        "resume_source": _resume_source(resume),
        "has_editor_content": bool(editor_content),
        "jd_title": (jd.title if jd else "") or (task.jd.title if task and task.jd else "") or "",
        "created_at": iso(report.created_at),
        "scores": {
            "overall": report.overall_score or 0,
            "ats": report.ats_score or 0,
            "impact": report.impact_score or 0,
            "clarity": report.clarity_score or 0,
            "match": report.match_score or 0,
            "risk": report.risk_score or 0,
        },
        "risk_level": report.risk_level or "yellow",
        # 双分制（仅 agentic 模式）：深度分与规则分并列展示。
        # fixed 模式为 None —— 前端据此隐藏"深度分析"卡片，不展示空壳。
        "depth_scores": extra.get("depth_scores") or None,
        "summary": report.summary or "",
        "findings": [serialize_finding(f) for f in sorted(report.findings, key=lambda x: x.sort_order or 0)],
        "missing_keywords": extra.get("missing_keywords") or [],
        "action_items": extra.get("action_items") or [],
        "knowledge_refs": extra.get("knowledge_refs") or [],
        # Agentic RAG 轨迹：fixed 模式为 None（前端据此决定是否展示"检索过程"面板）
        "agent_trace": extra.get("agent_trace") or None,
        # 同岗位对标（契约 §5.3）：由 api/reports.py 计算后传入。
        # 这里保持序列化器"无 DB 依赖"（纯函数），所以不在本函数里查 role_stats。
        # 为 None 表示样本不足或岗位族未知 —— 前端应整块隐藏，不要展示空壳。
        "benchmark": benchmark,
        "disclaimer": extra.get("disclaimer") or DISCLAIMER,
    }


# --------------------------------------------------------------------------- #
# 知识库 / 批次 / 反馈
# --------------------------------------------------------------------------- #
def serialize_knowledge_doc(doc: KnowledgeDoc, *, chunk_count: int | None = None) -> dict:
    payload = {
        "doc_id": doc.id,
        "scope": doc.scope or "role",
        "role_family": doc.role_family or "",
        "doc_type": doc.doc_type or "",
        "title": doc.title or "",
        "content": doc.content or "",
        "metadata": doc.doc_metadata or {},
        "created_at": iso(doc.created_at),
    }
    if chunk_count is not None:
        payload["chunk_count"] = chunk_count
    return payload


def serialize_batch_item(item: HrBatchItem, resume: Resume | None = None) -> dict:
    return {
        "resume_id": item.resume_id,
        "resume_title": (resume.title if resume else "") or "",
        "status": item.status or "pending",
        "overall_score": item.overall_score,
        "match_score": item.match_score,
        "risk_level": item.risk_level,
        "report_id": item.report_id,
        "summary": item.summary or "",
    }


def serialize_batch(batch: HrBatch, *, items: list[dict] | None = None) -> dict:
    return {
        "batch_id": batch.id,
        "status": batch.status or "pending",
        "total": batch.total or 0,
        "finished": batch.finished or 0,
        "items": items or [],
    }


def serialize_feedback(feedback) -> dict:
    return {
        "feedback_id": feedback.id,
        "report_id": feedback.report_id,
        "rating": feedback.rating,
        "comment": feedback.comment or "",
        "created_at": iso(feedback.created_at),
    }
