"""复现报告 B 类问题（B-1 ~ B-4）的端到端脚本。

用法（在 backend 目录下）：
    .\\.venv\\Scripts\\python.exe -m scripts.check_report_consistency

不依赖真实 LLM（走 mock 规则引擎），不写真实库：使用临时 sqlite + 临时上传目录。
每条检查都对应一条历史缺陷，修好后应为全部 PASS。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

# 必须在 import app.* 之前设定：临时库 + 临时上传目录
_TMP = Path(tempfile.mkdtemp(prefix="zr_consistency_"))
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(_TMP / 'check.db').as_posix()}"
os.environ["UPLOAD_DIR"] = (_TMP / "uploads").as_posix()
os.environ["EMBEDDING_PROVIDER"] = "hash"
for key in ("ALIYUN_API_KEY", "DEEPSEEK_API_KEY", "EMBEDDING_API_KEY", "EMBEDDING_FALLBACK_BASE_URL"):
    os.environ[key] = ""

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import AnalysisReport, JobDescription, Resume, User  # noqa: E402
from app.services import analyze, rag  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402
from app.workflow.engine import run_analysis  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
JD_FILE = SAMPLES / "sample_jd_backend.txt"

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"\n       {detail}" if detail else ""))


def _ensure_user(db, device_id: str = "consistency-check") -> User:
    user = db.execute(select(User).where(User.device_id == device_id)).scalar_one_or_none()
    if user is None:
        user = User(device_id=device_id, role="jobseeker", free_quota_used=0)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _ingest(db, user: User, raw_text: str, jd_text: str, title: str) -> tuple[str, str]:
    from app.agents import base as _base  # noqa: F401  确保 Agent 注册顺序已加载

    resume = Resume(
        user_id=user.id,
        title=title,
        raw_text=raw_text,
        parsed_json=parse_resume_text(raw_text).model_dump(),
        file_name=title,
        size_bytes=len(raw_text.encode("utf-8")),
    )
    jd = JobDescription(
        user_id=user.id,
        title="后端开发工程师",
        company="某科技",
        raw_text=jd_text,
        parsed_json=parse_jd_text(jd_text).model_dump(),
    )
    db.add_all([resume, jd])
    db.commit()
    return resume.id, jd.id


def _run(db, user: User, resume_id: str, jd_id: str) -> str:
    from app.models import AnalysisTask

    task = AnalysisTask(user_id=user.id, resume_id=resume_id, jd_id=jd_id, type="single", status="pending", progress=0.0)
    db.add(task)
    db.commit()
    task_id = task.id
    asyncio.run(run_analysis(task_id))
    db.expire_all()
    report = db.execute(select(AnalysisReport).where(AnalysisReport.task_id == task_id)).scalar_one_or_none()
    if report is None:
        raise RuntimeError("未产出报告")
    return report.id


def _report_dict(report: AnalysisReport) -> dict:
    from app.serializers import serialize_report

    from app.models import AnalysisTask

    with session_scope() as db:
        task = db.get(AnalysisTask, report.task_id)
        resume = db.get(Resume, task.resume_id) if task and task.resume_id else None
        jd = db.get(JobDescription, task.jd_id) if task and task.jd_id else None
        return serialize_report(report, task=task, resume=resume, jd=jd)


SCORE_IN_SUMMARY = re.compile(r"综合得分\s*(\d{1,3})\s*分")


def main() -> int:
    init_db()
    with session_scope() as db:
        seed_if_empty(db)
        user = _ensure_user(db)

    jd_text = JD_FILE.read_text(encoding="utf-8")
    weak = (SAMPLES / "sample_resume_1_weak.txt").read_text(encoding="utf-8")
    medium = (SAMPLES / "sample_resume_2_medium.txt").read_text(encoding="utf-8")
    strong = (SAMPLES / "sample_resume_3_strong.txt").read_text(encoding="utf-8")

    with session_scope() as db:
        user = _ensure_user(db)
        reports: dict[str, dict] = {}
        for title, text in (("weak", weak), ("medium", medium), ("strong", strong)):
            rid, jid = _ingest(db, user, text, jd_text, f"{title}.txt")
            report_id = _run(db, user, rid, jid)
            db.expire_all()
            reports[title] = _report_dict(db.get(AnalysisReport, report_id))

    # ---------------- B-1：摘要里的总分必须等于 scores.overall ----------------
    for title, rep in reports.items():
        matched = SCORE_IN_SUMMARY.search(rep["summary"] or "")
        ok = bool(matched) and int(matched.group(1)) == rep["scores"]["overall"]
        check(
            f"B-1 {title}: 摘要总分 == scores.overall",
            ok,
            f"overall={rep['scores']['overall']} 摘要={matched.group(0) if matched else '（无分数）'}",
        )

    # ---------------- B-2：风险等级不得与红色条目冲突 ----------------
    for title, rep in reports.items():
        reds = len([f for f in rep["findings"] if f["severity"] == "red"])
        level = rep["risk_level"]
        conflict = (reds >= 3 and level != "red") or (reds >= 1 and level == "green")
        check(
            f"B-2 {title}: 风险等级与红色条目一致",
            not conflict,
            f"red={reds} risk_score={rep['scores']['risk']} risk_level={level}",
        )

    # ---------------- B-3：不可分析的简历不得产出分数 ----------------
    from app.api.tasks import analyze_guard

    for label, text in (
        ("空文本", ""),
        ("纯空白", "   \n\n\t  "),
        ("英文简历", "John Smith\nBackend Engineer\n2020 - 2024 at Acme Corp\nBuilt services in Go."),
    ):
        resume = Resume(user_id="x", title=label, raw_text=text, parsed_json=parse_resume_text(text).model_dump())
        reason = analyze_guard(resume)
        check(f"B-3 守卫拦截「{label}」", reason is not None, (reason or "未拦截！")[:80])

    resume_ok = Resume(
        user_id="x", title="normal", raw_text=medium, parsed_json=parse_resume_text(medium).model_dump()
    )
    check("B-3 守卫放行正常简历", analyze_guard(resume_ok) is None, "")

    # ---------------- B-4：evidence.span 必须切出 quote 本身 ----------------
    for title, rep in reports.items():
        bad = []
        for f in rep["findings"]:
            ev = f.get("evidence") or {}
            span = ev.get("span")
            quote = (ev.get("quote") or "").strip()
            if not span:
                continue
            text = ev.get("_text")  # serialize_report 不带原文，这里只校验 span 长度合理
            if not quote:
                bad.append("空引文却给了 span")
            elif span[1] - span[0] <= 0:
                bad.append(f"span 非法 {span}")
        check(f"B-4 {title}: span 结构合法", not bad, "; ".join(bad))

    # 直接对原文校验（比只校验结构更严格）：所有红色条目的 span 切片必须等于 quote
    with session_scope() as db:
        for title in ("weak", "medium", "strong"):
            rep = reports[title]
            from app.models import AnalysisTask

            task = db.get(AnalysisTask, rep["task_id"])
            resume = db.get(Resume, task.resume_id)
            jd = db.get(JobDescription, task.jd_id)
            mismatched = []
            for f in rep["findings"]:
                ev = f.get("evidence") or {}
                span, quote = ev.get("span"), (ev.get("quote") or "").strip()
                if not span or not quote:
                    continue
                text = resume.raw_text if ev.get("source") == "resume" else jd.raw_text
                if text[span[0] : span[1]].strip() != quote:
                    mismatched.append(f"{quote[:24]!r} -> {text[span[0]:span[1]][:24]!r}")
            check(f"B-4 {title}: 所有 span 切片 == quote", not mismatched, "; ".join(mismatched[:3]))

    # ---------------- B-2 附加：所有红色必须能在**声明的来源**里定位 ----------------
    with session_scope() as db:
        for title in ("weak", "medium", "strong"):
            rep = reports[title]
            from app.models import AnalysisTask

            task = db.get(AnalysisTask, rep["task_id"])
            resume = db.get(Resume, task.resume_id)
            jd = db.get(JobDescription, task.jd_id)
            unlocatable = []
            for f in rep["findings"]:
                if f["severity"] != "red":
                    continue
                ev = f.get("evidence") or {}
                quote = (ev.get("quote") or "").strip()
                text = jd.raw_text if ev.get("source") == "jd" else resume.raw_text
                if not quote or quote not in text:
                    unlocatable.append(f"{quote[:24]!r}")
            check(f"契约 §5.1 {title}: 每条红色都能在声明来源定位", not unlocatable, "; ".join(unlocatable[:3]))

    # ---------------- 评分确定性：同一份简历连跑两次分数一致 ----------------
    with session_scope() as db:
        user = _ensure_user(db)
        rid, jid = _ingest(db, user, weak, jd_text, "det-1.txt")
        first = _run(db, user, rid, jid)
        db.expire_all()
        second = _run(db, user, rid, jid)
        db.expire_all()
        r1 = db.get(AnalysisReport, first)
        r2 = db.get(AnalysisReport, second)
        same = (r1.overall_score, r1.ats_score, r1.risk_score, r1.risk_level) == (
            r2.overall_score,
            r2.ats_score,
            r2.risk_score,
            r2.risk_level,
        )
        check(
            "确定性：同一简历两次诊断分数与风险等级一致",
            same,
            f"{r1.overall_score}/{r1.risk_level} vs {r2.overall_score}/{r2.risk_level}",
        )

    passed = len([r for r in results if r[0]])
    total = len(results)
    print("=" * 72)
    print(f"一致性检查：{passed}/{total} 项通过")
    print(f"（临时数据目录：{_TMP}）")
    failed = [r for r in results if not r[0]]
    if failed:
        for _, name, detail in failed:
            print(f"  ✗ {name}\n      {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
