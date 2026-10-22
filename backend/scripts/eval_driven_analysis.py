"""W1 验收：检索是否真的在**驱动分析**（而不只是事后挂依据）。

## 这个脚本回答一个问题

「Agentic RAG 从『检索依据』改成『驱动分析』之后，到底有没有变化？」

只看"报告里有几条引用"是不够的 —— 那个数字在 W1 之前也能被 qc 之后的
`knowledge_evidence` Agent 刷上去。W1 要证明的是另外两件事：

1. **分析阶段**（jd_match / ats / impact / risk）自己就产出了经校验的知识库引用，
   而不是等 qc 之后补挂；
2. 改写句真的**贴着检索到的规范**写（而不是套 `METRIC_HINTS` 硬编码模板）。

因此三个指标：

| 指标 | 含义 | 数据来源 |
|---|---|---|
| `analyzer_citation_rate` | 分析阶段带引用的结论占比 | `extra_json.agent_outputs.<node>.citations_count` |
| `final_citation_rate` | 最终报告里带引用的结论占比 | `report_findings.evidence_ref.citations` |
| `fabricated_number_rate` | 改写句里出现**原文没有的数字**的比例 | 逐条比对 `rewritten_text` 与 `raw_text` |

前两个指标区分「生成有据」与「结论可信」；第三个是 W1 引入提示词纪律后
必须**不恶化**的护栏（存量实测基线 10.2%，见 docs/ZhenResume_实测数据总表.md §4）。

## 用法（在 backend 目录下）

```bash
python -m scripts.eval_driven_analysis                 # 跑 3 份简历 × 1 JD
python -m scripts.eval_driven_analysis --limit 1       # 快速验证（约 2 分钟 / ¥0.2）
python -m scripts.eval_driven_analysis --reuse         # 只统计库里已有的 agentic 任务
```

`--reuse` 不产生任何 LLM 调用，用于回归时快速复查历史任务。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 必须在 import app.config 之前设置：本脚本只在 agentic 模式下有意义
os.environ.setdefault("WORKFLOW_ENGINE", "langgraph")
os.environ.setdefault("WORKFLOW_EXECUTOR", "inline")
os.environ["WORKFLOW_MODE"] = "agentic"

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisReport,
    AnalysisTask,
    JobDescription,
    Resume,
)
from app.workflow import run_analysis  # noqa: E402

# 数字匹配：整数 / 小数 / 百分比。只用于"这个数字在原文里有没有"。
#
# ⚠️ 必须排除**嵌在字母里的数字**（`P1` / `K8s` / `S3` / `99.9x`）：
# 第一版直接用 `\d+`，把「P1 事故」判成"编造了数字 1"、把「99.9x%」拆成 99.9 和 x，
# 3 份简历里报了 2 条假阳性（9.1%），实际编造为 0。检测器自己有偏会让护栏指标失真。
# 百分号跟数字算一个整体，与 `app/workflow/agent_nodes._NUM_RE` 保持同一口径。
_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?(?![A-Za-z])")

# 改写句里允许出现、但不算"编造"的占位符与结构词
_PLACEHOLDER_RE = re.compile(r"\[(?:补充|补充指标)[^\]]*\]")


def _numbers(text: str) -> list[str]:
    """抽取一段文本里的所有数字（去掉占位符内的内容）。"""
    cleaned = _PLACEHOLDER_RE.sub(" ", text or "")
    return _NUM_RE.findall(cleaned)


def fabricated_numbers(rewritten: str, resume_text: str) -> list[str]:
    """改写句里**原文完全没有**的数字。

    口径说明（与 docs/实测数据总表 §4 的 10.2% 对齐）：
    - 只比对数字字面，不做量纲归一（`100万` 与 `1000000` 视为不同）；
    - 占位符 `[补充指标：QPS]` 里的内容先剔除；
    - 原文任一位置出现过该数字字面即算"有据"（保守口径，不会冤枉模型）。
    """
    resume_nums = set(_numbers(resume_text))
    return [n for n in _numbers(rewritten) if n not in resume_nums]


def _citations_of(evidence_ref: dict | None) -> list[dict]:
    return list((evidence_ref or {}).get("citations") or [])


def measure_report(report: AnalysisReport, resume_text: str) -> dict:
    """对一份已落库报告算三个指标。"""
    extra = report.extra_json or {}
    agent_outputs = extra.get("agent_outputs") or {}

    analyzer_cites = 0
    analyzer_findings = 0
    per_node: dict[str, dict] = {}
    for node in ("jd_match", "ats", "impact", "risk"):
        out = agent_outputs.get(node) or {}
        n_cite = int(out.get("citations_count") or 0)
        n_find = int(out.get("agent_findings") or 0)
        analyzer_cites += n_cite
        analyzer_findings += n_find
        per_node[node] = {
            "agent_findings": n_find,
            "citations": n_cite,
            "retrieved_chunks": int(out.get("retrieved_chunks_count") or 0),
            "retrieval_rounds": (out.get("trace") or {}).get("retrieval_rounds"),
            "model_calls": (out.get("trace") or {}).get("model_calls"),
        }

    findings = list(report.findings or [])
    final_with_cite = 0
    by_source = {"analyzer": 0, "evidence": 0, "unknown": 0}
    fabricated_total = 0
    fabricated_examples: list[dict] = []

    for f in findings:
        cites = _citations_of(f.evidence_ref)
        if cites:
            final_with_cite += 1
        for c in cites:
            src = str(c.get("source") or "unknown")
            by_source[src if src in by_source else "unknown"] += 1

        bad = fabricated_numbers(f.rewritten_text or "", resume_text)
        if bad:
            fabricated_total += 1
            if len(fabricated_examples) < 5:
                fabricated_examples.append(
                    {
                        "title": f.title,
                        "rewritten_text": (f.rewritten_text or "")[:160],
                        "numbers_not_in_resume": bad,
                    }
                )

    n_findings = max(len(findings), 1)
    return {
        "report_id": report.id,
        "task_id": report.task_id,
        "findings": len(findings),
        "analyzer_findings": analyzer_findings,
        "analyzer_citations": analyzer_cites,
        "analyzer_citation_rate": round(analyzer_cites / max(analyzer_findings, 1), 4),
        "final_with_citation": final_with_cite,
        "final_citation_rate": round(final_with_cite / n_findings, 4),
        "citation_by_source": by_source,
        "fabricated_findings": fabricated_total,
        "fabricated_number_rate": round(fabricated_total / n_findings, 4),
        "fabricated_examples": fabricated_examples,
        "per_node": per_node,
        "agent_trace_status": (extra.get("agent_trace") or {}).get("status"),
    }


def _pick_samples(limit: int) -> list[tuple[str, str, str]]:
    """挑 (resume_id, jd_id, resume_text)，优先挑内容较丰富的简历。"""
    with session_scope() as db:
        jd = db.execute(
            select(JobDescription).order_by(JobDescription.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if jd is None:
            return []
        resumes = (
            db.execute(
                select(Resume)
                .where(Resume.raw_text.isnot(None))
                .order_by(Resume.created_at.desc())
                .limit(max(limit * 4, 12))
            )
            .scalars()
            .all()
        )
        # 按长度排序取"中位数偏上"的几份：太短的简历产不出几条结论，测不出指标
        usable = [r for r in resumes if len(r.raw_text or "") >= 400]
        usable.sort(key=lambda r: len(r.raw_text or ""))
        picked = usable[len(usable) // 2 :][:limit] if usable else []
        return [(r.id, jd.id, r.raw_text or "") for r in picked]


async def _run_one(resume_id: str, jd_id: str, resume_text: str) -> dict:
    with session_scope() as db:
        task = AnalysisTask(
            user_id=None,
            resume_id=resume_id,
            jd_id=jd_id,
            type="single",
            status="pending",
            progress=0.0,
            mode="agentic",
        )
        db.add(task)
        db.flush()
        task_id = task.id

    started = time.perf_counter()
    await run_analysis(task_id)
    elapsed = time.perf_counter() - started

    with session_scope() as db:
        report = db.execute(
            select(AnalysisReport).where(AnalysisReport.task_id == task_id)
        ).scalar_one_or_none()
        if report is None:
            return {"task_id": task_id, "error": "未产出报告", "elapsed_s": round(elapsed, 1)}
        row = measure_report(report, resume_text)
        row["elapsed_s"] = round(elapsed, 1)
        row["resume_chars"] = len(resume_text)
        return row


def _reuse_existing(limit: int) -> list[dict]:
    """只统计库里已有的 agentic 任务（零 LLM 调用）。"""
    rows: list[dict] = []
    with session_scope() as db:
        tasks = (
            db.execute(
                select(AnalysisTask)
                .where(AnalysisTask.mode == "agentic")
                .order_by(AnalysisTask.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        for task in tasks:
            report = db.execute(
                select(AnalysisReport).where(AnalysisReport.task_id == task.id)
            ).scalar_one_or_none()
            if report is None:
                continue
            resume = db.get(Resume, task.resume_id) if task.resume_id else None
            rows.append(measure_report(report, (resume.raw_text if resume else "") or ""))
    return rows


def _summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    if not ok:
        return {"cases": 0}

    def mean(key: str) -> float:
        vals = [float(r.get(key) or 0) for r in ok]
        return round(sum(vals) / len(vals), 4)

    total_findings = sum(int(r["findings"]) for r in ok)
    total_analyzer_cites = sum(int(r["analyzer_citations"]) for r in ok)
    total_analyzer_findings = sum(int(r["analyzer_findings"]) for r in ok)
    total_with_cite = sum(int(r["final_with_citation"]) for r in ok)
    total_fabricated = sum(int(r["fabricated_findings"]) for r in ok)

    return {
        "cases": len(ok),
        "errors": len(rows) - len(ok),
        # 加权口径（按条数）比"按报告取平均"更能反映真实占比
        "analyzer_citation_rate_weighted": round(
            total_analyzer_cites / max(total_analyzer_findings, 1), 4
        ),
        "final_citation_rate_weighted": round(total_with_cite / max(total_findings, 1), 4),
        "fabricated_number_rate_weighted": round(
            total_fabricated / max(total_findings, 1), 4
        ),
        "avg_findings": mean("findings"),
        "avg_analyzer_findings": mean("analyzer_findings"),
        "avg_elapsed_s": mean("elapsed_s"),
        "totals": {
            "findings": total_findings,
            "analyzer_findings": total_analyzer_findings,
            "analyzer_citations": total_analyzer_cites,
            "final_with_citation": total_with_cite,
            "fabricated": total_fabricated,
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3, help="跑几份简历")
    parser.add_argument(
        "--reuse", action="store_true", help="只统计库里已有的 agentic 任务（零 LLM 调用）"
    )
    parser.add_argument("--json-out", type=str, default="", help="结果落盘路径（可选）")
    args = parser.parse_args()

    print(
        f"WORKFLOW_MODE={settings.workflow_mode}｜ENGINE={settings.workflow_engine}"
        f"｜agentic_evidence={settings.agentic_evidence_enabled}"
    )
    assert settings.workflow_mode == "agentic", "本脚本只测 agentic 模式"

    if args.reuse:
        rows = _reuse_existing(args.limit)
        mode = "reuse"
    else:
        samples = _pick_samples(args.limit)
        if not samples:
            print("✗ 库里没有可用简历/JD，先跑 scripts.make_sample_data 或上传一份")
            return 1
        rows = []
        for i, (resume_id, jd_id, text) in enumerate(samples, 1):
            print(f"\n[{i}/{len(samples)}] 简历 {resume_id[:8]}（{len(text)} 字）…")
            row = await _run_one(resume_id, jd_id, text)
            rows.append(row)
            if "error" in row:
                print(f"  ✗ {row['error']}（{row['elapsed_s']}s）")
                continue
            print(
                f"  结论 {row['findings']} 条｜分析阶段带引用 {row['analyzer_citations']}/"
                f"{row['analyzer_findings']}｜最终带引用 {row['final_with_citation']} 条｜"
                f"编造数字 {row['fabricated_findings']} 条｜{row['elapsed_s']}s"
            )
            for node, d in row["per_node"].items():
                print(
                    f"    {node:9s} 结论 {d['agent_findings']}｜引用 {d['citations']}｜"
                    f"检索切片 {d['retrieved_chunks']}｜轮次 {d['retrieval_rounds']}｜往返 {d['model_calls']}"
                )
        mode = "live"

    summary = _summarize(rows)
    print("\n=== 汇总 ===")
    if not summary.get("cases"):
        print("没有可统计的报告")
        return 1
    print(f"  案例数                : {summary['cases']}（失败 {summary['errors']}）")
    print(
        f"  分析阶段引用覆盖率    : {summary['analyzer_citation_rate_weighted']:.1%}"
        "  ← W1 核心指标（fixed 线恒为 0%）"
    )
    print(f"  最终报告引用覆盖率    : {summary['final_citation_rate_weighted']:.1%}")
    print(
        f"  改写句编造数字比例    : {summary['fabricated_number_rate_weighted']:.1%}"
        "  ← 护栏：不应高于存量基线 10.2%"
    )
    print(f"  平均结论数 / 耗时     : {summary['avg_findings']:.1f} 条 / {summary['avg_elapsed_s']:.0f}s")
    print(f"  明细                  : {json.dumps(summary['totals'], ensure_ascii=False)}")

    # 编造样例（便于人工抽查，不参与判定）
    for row in rows:
        for ex in (row.get("fabricated_examples") or [])[:2]:
            print(f"  ! 编造样例：{ex['title']} → {ex['numbers_not_in_resume']}")

    out = args.json_out or str(
        BACKEND_DIR / "eval" / "runs" / f"eval_driven_{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        json.dumps({"mode": mode, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[saved] {out}")

    # 判定：W1 生效的最低要求 —— 分析阶段必须真的产出引用
    if summary["analyzer_citation_rate_weighted"] <= 0:
        print("\n✗ W1 未生效：分析阶段没有任何经校验的知识库引用")
        return 1
    print("\n✓ W1 生效：分析阶段产出了经服务端校验的知识库引用")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
