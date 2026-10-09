"""诊断报告检查器：对指定简历 + JD 离线跑一遍完整工作流，打印报告全文。

用途（对应 aaa.md 结尾的建议）：
在接真实 LLM 之前，先用它批量跑真实简历，人工评估报告质量是否达到可交付水平。

用法（在 backend 目录下）：
    python -m scripts.inspect --resume samples/sample_resume_1_weak.txt --jd samples/sample_jd_backend.txt
    python -m scripts.inspect --all                        # 跑 samples 目录下所有简历
    python -m scripts.inspect --all --category risk        # 只看某类发现项
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session_scope  # noqa: E402
from app.models import AnalysisReport, AnalysisTask, JobDescription, Resume  # noqa: E402
from app.serializers import serialize_report  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402
from app.workflow import run_workflow  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SEV_MARK = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


def _prepare(text_resume: str, text_jd: str) -> tuple[str, str]:
    """写入临时简历与 JD 记录，返回 (resume_id, jd_id)。"""
    with session_scope() as db:
        resume = Resume(
            user_id=None,
            title="inspect-resume",
            raw_text=text_resume,
            parsed_json=parse_resume_text(text_resume).model_dump(),
        )
        jd = JobDescription(
            user_id=None,
            title="inspect-jd",
            company="",
            raw_text=text_jd,
            parsed_json=parse_jd_text(text_jd).model_dump(),
        )
        db.add(resume)
        db.add(jd)
        db.flush()
        return resume.id, jd.id


def _run_one(label: str, text_resume: str, text_jd: str, category: str | None, show_full: bool) -> dict:
    resume_id, jd_id = _prepare(text_resume, text_jd)

    with session_scope() as db:
        task = AnalysisTask(user_id=None, resume_id=resume_id, jd_id=jd_id, type="single", status="pending")
        db.add(task)
        db.flush()
        task_id = task.id

    report_id = asyncio.run(run_workflow(task_id))

    with session_scope() as db:
        report = db.get(AnalysisReport, report_id)
        task = db.get(AnalysisTask, task_id)
        resume = db.get(Resume, resume_id)
        jd = db.get(JobDescription, jd_id)
        payload = serialize_report(report, task=task, resume=resume, jd=jd)

    print(f"\n{'=' * 78}")
    print(f"简历：{label}")
    print(f"{'=' * 78}")
    scores = payload["scores"]
    print(
        f"总分 {scores['overall']} ｜ ATS {scores['ats']} ｜ 影响力 {scores['impact']} ｜ "
        f"清晰度 {scores['clarity']} ｜ 竞争力 {scores['match']} ｜ 风险安全度 {scores['risk']}（{payload['risk_level']}）"
    )
    print(f"摘要：{payload['summary']}")
    print(f"缺失关键词：{'、'.join(payload['missing_keywords'][:12]) or '无'}")

    findings = payload["findings"]
    if category:
        findings = [f for f in findings if f["category"] == category]

    print(f"\n发现项 {len(findings)} 条" + (f"（已过滤 category={category}）" if category else ""))
    for i, f in enumerate(findings, 1):
        print(f"\n{i}. {SEV_MARK.get(f['severity'], '')} [{f['category']}] {f['title']}")
        if f["original_text"]:
            print(f"   原文：{f['original_text'][:100]}")
        print(f"   问题：{f['issue'][:160]}")
        ev = f["evidence"]
        span = ev.get("span")
        span_text = f"字符 {span[0]}–{span[1]}" if isinstance(span, list) else "未定位"
        print(f"   证据：{ev.get('source')}「{(ev.get('quote') or '')[:60]}」（{span_text}）")
        if show_full:
            print(f"   建议：{f['suggestion']}")
            print(f"   改写：{f['rewritten_text']}")

    print(f"\n行动清单：")
    for i, action in enumerate(payload["action_items"], 1):
        print(f"  {i}. {action}")
    print(f"知识库引用：{'、'.join(r['title'] for r in payload['knowledge_refs']) or '无'}")

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="离线诊断并打印完整报告")
    parser.add_argument("--resume", help="简历文本文件路径")
    parser.add_argument("--jd", help="JD 文本文件路径")
    parser.add_argument("--all", action="store_true", help="跑 samples 目录下所有简历")
    parser.add_argument("--category", help="只显示某类发现项：ats / impact / clarity / risk / gap")
    parser.add_argument("--brief", action="store_true", help="不打印建议与改写句")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    jd_path = Path(args.jd) if args.jd else SAMPLES / "sample_jd_backend.txt"
    jd_text = jd_path.read_text(encoding="utf-8")

    if args.all:
        resumes = sorted(SAMPLES.glob("sample_resume_*.txt"))
        if not resumes:
            print(f"samples 目录下没有简历文件：{SAMPLES}")
            return 1
        for path in resumes:
            _run_one(path.name, path.read_text(encoding="utf-8"), jd_text, args.category, not args.brief)
        return 0

    if not args.resume:
        parser.error("需要 --resume 或 --all")
    resume_path = Path(args.resume)
    if not resume_path.is_absolute():
        resume_path = SAMPLES / args.resume if not resume_path.exists() else resume_path
    if not resume_path.exists():
        print(f"简历文件不存在：{resume_path}")
        return 1

    _run_one(resume_path.name, resume_path.read_text(encoding="utf-8"), jd_text, args.category, not args.brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
