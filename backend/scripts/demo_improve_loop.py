"""演示核心价值：同一份简历的「草稿版」与「改进版」分数对比。

对应 aaa.md §六.4 的「二次诊断：修改后重新上传，对比分数变化」。
它不经过浏览器，直接复用编辑器创建简历 → 8-Agent 工作流 → 报告的完整链路，
用来快速回答一个产品问题：**按报告建议改完之后，分数真的会涨吗？涨多少？**

用法（在 backend 目录下）：
    python -m scripts.demo_improve_loop
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session_scope  # noqa: E402
from app.models import AnalysisReport, AnalysisTask, JobDescription, Resume  # noqa: E402
from app.schemas import EditorContent  # noqa: E402
from app.serializers import serialize_report  # noqa: E402
from app.services import resume_render  # noqa: E402
from app.services.parse_rules import parse_jd_text  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402
from app.workflow import run_workflow  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# --------------------------------------------------------------------------- #
# 草稿版：只写职责、没有数字、技能栏缺失、有一段空窗期
# --------------------------------------------------------------------------- #
DRAFT: dict = {
    "basics": {
        "name": "张伟",
        "target_title": "后端开发工程师",
        "phone": "138-1234-5678",
        "email": "zhangwei@example.com",
        "city": "杭州",
        "links": [],
    },
    "summary": "3 年后端开发经验，工作认真负责，有良好的团队协作精神。",
    "work": [
        {
            "company": "杭州某科技有限公司",
            "title": "后端开发工程师",
            "start": "2022.07",
            "end": "至今",
            "location": "杭州",
            "highlights": [
                "负责订单核心接口的开发与维护",
                "参与系统性能优化",
                "使用 Redis 做缓存",
            ],
        },
        {
            "company": "上海某信息技术有限公司",
            "title": "后端开发实习生",
            "start": "2021.07",
            "end": "2021.12",
            "location": "上海",
            "highlights": ["参与后台管理系统开发"],
        },
    ],
    "projects": [],
    "education": [{"school": "某某大学", "degree": "本科", "major": "计算机科学与技术", "start": "2017.09", "end": "2021.06"}],
    "skills": [],
    "certificates": ["CET-6"],
    "awards": [],
    "custom_sections": [],
}

# --------------------------------------------------------------------------- #
# 改进版：按报告建议改 —— 补量化、补技能栏与 JD 关键词、补项目、说明空窗期
# --------------------------------------------------------------------------- #
IMPROVED: dict = {
    "basics": {
        "name": "张伟",
        "target_title": "后端开发工程师",
        "phone": "138-1234-5678",
        "email": "zhangwei@example.com",
        "city": "杭州",
        "links": ["github.com/zhangwei"],
    },
    "summary": "3 年后端开发经验，专注订单与交易链路的高并发场景。主导过订单查询接口重构，P99 延迟下降 75%。",
    "work": [
        {
            "company": "杭州某科技有限公司",
            "title": "后端开发工程师",
            "start": "2022.07",
            "end": "至今",
            "location": "杭州",
            "highlights": [
                "主导订单核心接口的重构，引入本地缓存与批量查询，支撑日均 200 万次调用，接口 P99 延迟从 480ms 降至 120ms",
                "负责系统性能优化，定位并解决 3 张高频表的全表扫描问题，高峰期数据库 CPU 使用率从 85% 降至 45%",
                "使用 Redis 设计缓存方案，缓存命中率稳定在 96%，缓存穿透导致的慢查询归零",
                "参与微服务拆分，落地 4 个领域服务，发布效率提升 2 倍（基于 Kubernetes 与 Docker）",
            ],
        },
        {
            "company": "上海某信息技术有限公司",
            "title": "后端开发实习生",
            "start": "2021.07",
            "end": "2021.12",
            "location": "上海",
            "highlights": ["参与后台管理系统开发，独立完成 6 个报表模块，将人工统计耗时从 4 小时压缩至 10 分钟"],
        },
    ],
    "projects": [
        {
            "name": "订单系统重构",
            "role": "核心开发",
            "start": "2023.03",
            "end": "2023.09",
            "tech": ["Java", "Spring Boot", "Redis", "Kafka", "Kubernetes"],
            "highlights": [
                "主导订单查询接口重构，引入本地缓存与异步化改造，接口 P99 延迟从 480ms 降至 120ms",
                "使用 Kafka 重构订单异步通知链路，消息积压问题归零，日均处理 200 万条消息",
            ],
        }
    ],
    "education": [{"school": "某某大学", "degree": "本科", "major": "计算机科学与技术", "start": "2017.09", "end": "2021.06"}],
    "skills": ["Java", "Spring Boot", "Spring Cloud", "MySQL", "Redis", "Kafka", "Docker", "Kubernetes", "微服务", "高并发"],
    "certificates": ["CET-6"],
    "awards": [],
    "custom_sections": [
        {"title": "空窗期说明", "items": ["2021.12 – 2022.07 全职备考并完成 Spring Cloud 微服务实战课程，独立完成一套订单系统的开发"]}
    ],
}

DIMENSIONS = [
    ("综合得分", "overall"),
    ("ATS 匹配", "ats"),
    ("影响力", "impact"),
    ("清晰度", "clarity"),
    ("竞争力差距", "match"),
    ("风险安全度", "risk"),
]


def _run_version(label: str, payload: dict, jd_id: str) -> dict:
    content = EditorContent.model_validate(payload)

    with session_scope() as db:
        resume = Resume(
            user_id=None,
            title=f"演示-{label}",
            raw_text=resume_render.render_plain_text(content),
            parsed_json={**resume_render.to_parsed_resume(content).model_dump(), "editor_content": content.model_dump()},
        )
        db.add(resume)
        db.flush()
        resume_id = resume.id

        task = AnalysisTask(user_id=None, resume_id=resume_id, jd_id=jd_id, type="single", status="pending")
        db.add(task)
        db.flush()
        task_id = task.id

    report_id = asyncio.run(run_workflow(task_id))
    if not report_id:
        print(f"[{label}] 工作流未产出报告")
        return {}

    with session_scope() as db:
        report = db.get(AnalysisReport, report_id)
        task = db.get(AnalysisTask, task_id)
        resume = db.get(Resume, resume_id)
        jd = db.get(JobDescription, jd_id)
        return serialize_report(report, task=task, resume=resume, jd=jd)


def main() -> int:
    init_db()
    with session_scope() as db:
        seed_if_empty(db)
        jd_text = (SAMPLES / "sample_jd_backend.txt").read_text(encoding="utf-8")
        jd = JobDescription(
            user_id=None,
            title="后端开发工程师",
            company="某互联网科技有限公司",
            raw_text=jd_text,
            parsed_json=parse_jd_text(jd_text).model_dump(),
        )
        db.add(jd)
        db.flush()
        jd_id = jd.id

    draft = _run_version("草稿版", DRAFT, jd_id)
    improved = _run_version("改进版", IMPROVED, jd_id)
    if not draft or not improved:
        return 1

    def counts(report: dict) -> dict:
        return {s: len([f for f in report["findings"] if f["severity"] == s]) for s in ("red", "yellow", "green")}

    dc, ic = counts(draft), counts(improved)

    print()
    print("=" * 74)
    print("二次诊断对比：按报告建议修改后，分数变化")
    print("=" * 74)
    print(f"{'维度':<14}{'草稿版':>10}{'改进版':>10}{'变化':>10}")
    print("-" * 74)
    for label, key in DIMENSIONS:
        before, after = draft["scores"][key], improved["scores"][key]
        delta = after - before
        arrow = f"+{delta}" if delta > 0 else str(delta)
        print(f"{label:<14}{before:>10}{after:>10}{arrow:>10}")

    print("-" * 74)
    print(f"{'高风险':<12}{dc['red']:>10}{ic['red']:>10}")
    print(f"{'需优化':<12}{dc['yellow']:>10}{ic['yellow']:>10}")
    print(f"{'表现良好':<12}{dc['green']:>10}{ic['green']:>10}")
    print(f"{'缺失关键词':<12}{len(draft['missing_keywords']):>10}{len(improved['missing_keywords']):>10}")
    print(f"{'发现项总数':<12}{len(draft['findings']):>10}{len(improved['findings']):>10}")
    print()

    print("草稿版最严重的三个问题：")
    for f in draft["findings"][:3]:
        print(f"  · [{f['severity']}] {f['title']}")
    print()
    print("改进版仍然存在的问题（说明报告不是「改完就满分」）：")
    remaining = [f for f in improved["findings"] if f["severity"] in ("red", "yellow")]
    if remaining:
        for f in remaining[:4]:
            print(f"  · [{f['severity']}] {f['title']}")
    else:
        print("  （无）")
    print()

    overall_delta = improved["scores"]["overall"] - draft["scores"]["overall"]
    print("=" * 74)
    print(f"综合得分：{draft['scores']['overall']} → {improved['scores']['overall']}（{overall_delta:+d} 分）")
    print("=" * 74)

    if overall_delta <= 0:
        print("\n[FAIL] 改进版的综合得分没有提升 —— 评分模型可能已经失真，需要检查。")
        return 1
    print("\n[OK] 按建议修改后分数确实提升，「二次诊断对比分数」这个卖点成立。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
