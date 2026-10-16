"""岗位族聚合统计（方案 v2 §2）：HR 数据唯一被允许落库的形态。

用法（backend 目录下）：

    python -m scripts.aggregate_stats                      # 干跑：算出来只打印，不入库
    python -m scripts.aggregate_stats --apply              # 写入 role_stats
    python -m scripts.aggregate_stats --period 2026-Q1 --apply

## 为什么只存聚合量

HR 导入的候选人简历属于"为招聘目的"处理的数据，拿去做范文是**目的外使用**；
但**聚合到个体消失**之后就不再是个人信息。这个脚本就是把原始报告压成聚合量的地方 ——
**全程不把 `resumes.raw_text` 写进任何表**，中间量在内存里算完即丢。

## ⚠️ 两道闸门（都不是优化项，是"这条路合法"的前提）

1. **`sample_size >= 10`**：某格样本不足就不出数。否则"某公司那年唯一做订单系统的后端"
   会被反推出来，就又变回个人信息了。
2. **指标本身也做最小样本**：某个指标只有零星几条数据时不输出该指标（避免均值被单条主导）。

## 指标口径

| key | 含义 | 从哪算 |
|---|---|---|
| `score_p75` | overall_score 的 75 分位（"高分线"） | `analysis_reports.overall_score` |
| `highlight_avg_chars` | 经历要点平均字数 | `resumes.parsed_json` 的 work/project highlights |
| `quantified_per_project` | 每段经历平均含几个量化数字 | 同上（数数字） |
| `action_verb_variety` | 动词多样性（要点首词的去重数量） | 同上 |
| `star_completeness` | 要点里"有动作 + 有数字"的比例（STAR 的 S/T 难以机器判定，用此近似） | 同上 |
| `jd_keyword_coverage` | 命中 JD 关键词的比例 | `jds.parsed_json` 的关键词列表 ∩ 简历文本 |
| `top_skills` | 出现频率 top20 技能 | `resumes.parsed_json.skills` |
| `top_missing_keywords` | 最常缺的 JD 关键词 top10 | 同上（未命中的关键词频次） |
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import AnalysisReport, AnalysisTask, JobDescription, Resume, RoleStats  # noqa: E402

MIN_SAMPLE = 10          # 闸门①：每格最少样本数（k-匿名思路）
MIN_METRIC_SAMPLE = 5    # 闸门②：单个指标的最少有效样本
TOP_N = 20
DIGIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|ms|s|秒|万|亿|倍|人|次|条|个|qps|tps)?", re.IGNORECASE)

# 岗位族关键词映射（人工可改）。命中顺序即优先级。
ROLE_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("算法", ("算法", "机器学习", "深度学习", "推荐", "nlp", "cv", "模型", "数据挖掘", "大模型")),
    ("数据", ("数据分析", "数据仓库", "数仓", "bi", "etl", "sql 分析", "指标体系")),
    ("前端", ("前端", "react", "vue", "web", "h5", "小程序", "typescript")),
    ("测试", ("测试", "qa", "自动化测试", "质量保障")),
    ("运维", ("运维", "sre", "devops", "k8s", "kubernetes", "容器", "监控")),
    ("产品", ("产品经理", "产品设计", "需求分析", "prd")),
    ("运营", ("运营", "增长", "活动策划", "用户运营")),
    ("后端", ("后端", "服务端", "java", "golang", "go 语言", "spring", "微服务", "高并发", "mysql")),
]


def guess_role_family(jd_title: str, jd_text: str) -> str:
    """从 JD 标题+正文判定岗位族。

    用**命中计数**而不是"先匹配到谁算谁"—— 实测踩过：后端 JD 里写了
    "了解 Kubernetes 与微服务治理"，而 运维 的关键词里有 kubernetes，
    若按顺序先匹配就会被判成运维，**把整格数据归错岗位**。
    计数法下：后端命中 java/spring/mysql/微服务 4 次，运维命中 kubernetes 1 次 → 后端胜出。
    计数相同时按 ROLE_FAMILY_RULES 的顺序（即优先级）取先者。
    """
    haystack = f"{jd_title or ''}\n{(jd_text or '')[:2000]}".lower()
    best, best_hits = "其他", 0
    for family, keys in ROLE_FAMILY_RULES:
        hits = sum(1 for k in keys if k in haystack)
        if hits > best_hits:
            best, best_hits = family, hits
    return best


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


def _highlights(parsed_resume: dict | None) -> list[str]:
    """把 parsed_json 里的经历要点拍平成一个列表（work + projects 的 highlights）。"""
    out: list[str] = []
    data = parsed_resume or {}
    for section in ("work", "projects"):
        for entry in data.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("highlights") or []:
                if isinstance(h, str) and h.strip():
                    out.append(h.strip())
    return out


def _jd_keywords(parsed_jd: dict | None) -> list[str]:
    """从 parsed_jd 里取出"关键词"列表。

    防御式实现：不同版本的 schema 字段名可能是 keywords / must_have / requirements / skills，
    这里把第一个"字符串列表"字段当关键词用 —— 读不到就返回空（该指标自动跳过）。
    """
    data = parsed_jd or {}
    for key in ("keywords", "must_have", "requirements", "skills", "responsibilities"):
        value = data.get(key)
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return [v.strip() for v in value if v.strip()]
    return []


def collect_cell(rows: list[tuple]) -> dict:
    """把一格（同一岗位族 + 同一周期）的报告压成聚合量。

    `rows` 每项为 (overall_score, parsed_resume, parsed_jd, raw_text)
    —— 注意 `raw_text` 只在内存里用于关键词命中计算，**不会进任何表**。
    """
    scores = [r[0] for r in rows if r[0] is not None]
    highlight_lens: list[int] = []
    quantified: list[int] = []
    verb_counts: list[int] = []      # 每份简历的"动词去重数"，最后取平均
    star_ratios: list[float] = []    # 每份简历的"结构化比例"，最后取平均
    coverage: list[float] = []
    skills: Counter = Counter()
    missing: Counter = Counter()

    for _score, parsed_resume, parsed_jd, raw_text in rows:
        texts = _highlights(parsed_resume)
        # ⚠️ 必须**按每份简历**算再取平均，不能把整格 40 份的动词堆在一起数去重数量：
        # 后者是 population 级指标（40 份能数出 9 种），与单份简历的 1~3 种不可比，
        # 会让"同岗位对标"永远报"你更差"。指标口径必须与 services/benchmark.py 一致。
        if texts:
            verb_counts.append(len({t[:2] for t in texts}))
            hits = sum(1 for t in texts if DIGIT_RE.findall(t) and len(t) >= 12)
            star_ratios.append(hits / len(texts))
        for t in texts:
            highlight_lens.append(len(t))
            quantified.append(len(DIGIT_RE.findall(t)))

        for s in (parsed_resume or {}).get("skills") or []:
            if isinstance(s, str) and s.strip():
                skills[s.strip()] += 1

        kws = _jd_keywords(parsed_jd)
        if kws:
            haystack = (raw_text or "").lower()
            hit = [k for k in kws if k.lower() in haystack]
            miss = [k for k in kws if k.lower() not in haystack]
            coverage.append(len(hit) / len(kws))
            missing.update(miss)

    def avg(values: list) -> float | None:
        # 闸门②：单个指标样本太少就不输出，避免被一两条数据带偏
        if len(values) < MIN_METRIC_SAMPLE:
            return None
        return round(sum(values) / len(values), 1)

    metrics: dict = {
        "highlight_avg_chars": avg(highlight_lens),
        "quantified_per_project": avg(quantified) if len(quantified) >= MIN_METRIC_SAMPLE else None,
        "action_verb_variety": avg(verb_counts),
        "star_completeness": (
            round(sum(star_ratios) / len(star_ratios), 2) if len(star_ratios) >= MIN_METRIC_SAMPLE else None
        ),
        "jd_keyword_coverage": round(sum(coverage) / len(coverage), 2) if len(coverage) >= MIN_METRIC_SAMPLE else None,
        "top_skills": [k for k, _ in skills.most_common(TOP_N)],
        "top_missing_keywords": [k for k, _ in missing.most_common(10)],
        # 每个指标的样本量也记下来，方便排查"这个数是怎么来的"
        "_metric_samples": {
            "highlights": len(highlight_lens),
            "coverage": len(coverage),
            "reports": len(scores),
        },
    }
    p75 = None
    if len(scores) >= MIN_METRIC_SAMPLE:
        # statistics.quantiles 需要至少 2 个点；样本少时退化为最大值
        p75 = int(statistics.quantiles(scores, n=4)[2]) if len(scores) >= 4 else max(scores)
    return {"sample_size": len(rows), "score_p75": p75, "metrics": metrics}


def build(period: str) -> dict[str, dict]:
    """扫描该周期的已完成报告，按岗位族聚合成 {role_family: cell}。"""
    cells: dict[str, list[tuple]] = {}
    with session_scope() as db:
        rows = db.execute(
            select(AnalysisReport, AnalysisTask, Resume, JobDescription)
            .join(AnalysisTask, AnalysisTask.id == AnalysisReport.task_id)
            .join(Resume, Resume.id == AnalysisTask.resume_id)
            .outerjoin(JobDescription, JobDescription.id == AnalysisTask.jd_id)
            .where(AnalysisTask.status == "done")
        ).all()
        for report, _task, resume, jd in rows:
            if period and period not in _period_of(resume, report):
                continue
            family = guess_role_family(
                (jd.title if jd else "") or "",
                (jd.raw_text if jd else "") or "",
            )
            cells.setdefault(family, []).append(
                (report.overall_score, resume.parsed_json, jd.parsed_json if jd else None, resume.raw_text)
            )
    return {family: collect_cell(rows) for family, rows in cells.items()}


def _period_of(resume: Resume, report: AnalysisReport) -> str:
    """按报告的生成时间归周期（没有 generated_at 就用简历创建时间兜底）。"""
    stamp = getattr(report, "generated_at", None) or getattr(resume, "created_at", None)
    if stamp is None:
        return current_period()
    return current_period(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))


def main() -> int:
    parser = argparse.ArgumentParser(description="岗位族聚合统计（k≥10 闸门）")
    parser.add_argument("--period", default="", help="周期，如 2026-Q1；缺省=当前季度")
    parser.add_argument("--apply", action="store_true", help="写入 role_stats；不加则只打印")
    parser.add_argument("--min-sample", type=int, default=MIN_SAMPLE)
    args = parser.parse_args()

    period = args.period or current_period()
    init_db()
    cells = build(period)

    print(f"周期 {period}：扫描到 {len(cells)} 个岗位族")
    print("-" * 74)
    written = skipped = 0
    for family, cell in sorted(cells.items(), key=lambda kv: -kv[1]["sample_size"]):
        n = cell["sample_size"]
        if n < args.min_sample:
            skipped += 1
            print(f"  {family:<6} 样本 {n:>3} → **不出数**（低于闸门 k={args.min_sample}，避免可识别）")
            continue
        m = cell["metrics"]
        print(f"  {family:<6} 样本 {n:>3} | 高分线 {cell['score_p75']} | 要点均长 {m['highlight_avg_chars']} 字"
              f" | 每段量化 {m['quantified_per_project']} 个 | 动词 {m['action_verb_variety']} 种"
              f" | 关键词覆盖 {m['jd_keyword_coverage']}")
        if args.apply:
            with session_scope() as db:
                row = db.execute(
                    select(RoleStats).where(RoleStats.role_family == family, RoleStats.period == period)
                ).scalar_one_or_none()
                if row is None:
                    row = RoleStats(role_family=family, period=period)
                    db.add(row)
                row.sample_size = n
                row.score_p75 = cell["score_p75"]
                row.metrics = m
                row.generated_at = datetime.now(timezone.utc)
                db.flush()
            written += 1

    print("-" * 74)
    print(f"入库 {written} 格，被闸门挡下 {skipped} 格" + ("" if args.apply else "（干跑，未写入）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
