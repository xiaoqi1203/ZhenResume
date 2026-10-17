"""Agentic RAG 专项评测：固定检索 vs Agent 自主多轮检索。

**对照的两条臂**（同一批简历 / 同一个 JD / 同一份知识库）：

| | fixed 臂 | agentic 臂 |
|---|---|---|
| 检索时机 | 进图前**一次** | 由 Agent 自主决定（0~4 轮） |
| query | `rag.build_query` = JD 标题/技能/关键词 + 简历技能（**JD 驱动**） | 模型按"证据需求"自拟（**结论驱动**） |
| 检索量 | top_k=5 固定 | 按需，可下钻 read_chunk |
| 引用 | 无 finding 级引用（报告级 top-3 元数据） | finding 级 `{chunk_id, quote}`，服务端逐字校验 |

**指标**：
- `gold_recall`：知识库中真正能支撑该案例结论的切片，有多少被检索到（0~1）；
- `citation_gold_rate`：Agent 给出的引用里，落在 gold 切片的比例（引用是否"引到点子上"）；
- `verified_rate`：服务端逐字校验通过率（quote 能否在切片里定位）；
- 轮次 / 成本 / 延迟。

用法（在 backend 目录下）：
    python -m scripts.eval_rag            # 跑全部案例
    python -m scripts.eval_rag --limit 3  # 只跑前 3 个（快速验证）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.agentic import AgentInputs, BaseAgent, get_spec  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import KnowledgeChunk, KnowledgeDoc  # noqa: E402
from app.services import analyze, rag  # noqa: E402
from app.services.citation import verify_citations  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

FIXTURES = BACKEND_DIR / "eval" / "sort_fixtures"
OUT_DIR = BACKEND_DIR / "eval" / "runs"

# 每个案例：用哪份简历 + 哪些切片是"真正能支撑该简历结论"的 gold。
# gold 按（文档标题, 切片序号）声明，运行时解析成真实 chunk_id（避免手写 UUID 出错）。
CASES: list[dict] = [
    {"id": "rag_A00", "resume": "sort_A00.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_A03", "resume": "sort_A03.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_A05", "resume": "sort_A05.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0), ("经历要点的黄金表达结构", 1)]},
    {"id": "rag_B01", "resume": "sort_B01.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_B03", "resume": "sort_B03.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_C01", "resume": "sort_C01.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("ATS 关键词匹配的实际工作方式", 0)]},
    {"id": "rag_C07", "resume": "sort_C07.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_D02", "resume": "sort_D02.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0), ("经历要点的黄金表达结构", 1)]},
    {"id": "rag_D04", "resume": "sort_D04.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0)]},
    {"id": "rag_D06", "resume": "sort_D06.txt", "gold": [("后端工程师能力模型与量化标准", 0), ("后端工程师能力模型与量化标准", 1), ("经历要点的黄金表达结构", 0), ("经历要点的黄金表达结构", 1)]},
]


def resolve_gold() -> dict[tuple[str, int], str]:
    """(文档标题, 切片序号) -> chunk_id。切片按创建顺序（与入库顺序一致）。"""
    mapping: dict[tuple[str, int], str] = {}
    with session_scope() as db:
        for chunk in db.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.created_at)).scalars().all():
            doc = db.get(KnowledgeDoc, chunk.doc_id)
            if doc is None:
                continue
            index = sum(1 for key in mapping if key[0] == doc.title)
            mapping[(doc.title, index)] = chunk.id
    return mapping


def fixed_arm(resume_text: str, jd_text: str, parsed_resume, parsed_jd, top_k: int = 5) -> dict:
    """当前产品链路的检索方式：一次、JD 驱动、top_k 固定。"""
    t0 = time.perf_counter()
    query = rag.build_query(parsed_jd.model_dump(), parsed_resume.model_dump())
    with session_scope() as db:
        hits = rag.search(db, query, top_k=top_k, role_family="backend")
    return {
        "query": query,
        "chunk_ids": [h.get("chunk_id") for h in hits],
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def agentic_arm(resume_text: str, jd_text: str, parsed_resume, parsed_jd, findings: list[dict]) -> dict:
    inputs = AgentInputs(
        resume_text=resume_text,
        jd_text=jd_text,
        parsed_resume=parsed_resume,
        parsed_jd=parsed_jd,
        role_family="backend",
        extra={
            "missing_keywords": analyze.missing_keywords(parsed_resume, parsed_jd, resume_text)[0][:12],
            "findings": [
                {"index": i, "title": f.get("title") or "", "issue": (f.get("issue") or "")[:120]}
                for i, f in enumerate(findings)
            ],
        },
    )
    run = await BaseAgent(get_spec("knowledge_evidence")).run(inputs)
    retrieved: list[str] = []
    for hit in run.trace.get("retrieval_hits") or []:
        retrieved.extend(hit.get("chunk_ids") or [])
    with session_scope() as db:
        verified, vstats = verify_citations(db, (run.output or {}).get("citations") or [])
    return {
        "retrieved": list(dict.fromkeys(retrieved)),
        "citations": [v["chunk_id"] for v in verified],
        "queries": run.trace.get("retrieval_queries") or [],
        "rounds": run.trace.get("retrieval_rounds") or 0,
        "blocked": run.trace.get("retrieval_blocked") or 0,
        "model_calls": run.trace.get("model_calls") or 0,
        "verify": vstats,
        "cost": run.cost,
        "latency_ms": run.latency_ms,
        "status": run.status,
        "reasoning": (run.output or {}).get("reasoning") or "",
    }


def ratio(hit: list[str], gold: list[str]) -> float:
    if not gold:
        return 0.0
    return len(set(hit) & set(gold)) / len(set(gold))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    cases = CASES[: args.limit] if args.limit else CASES

    gold_map = resolve_gold()
    jd_text = (FIXTURES / "jd_backend.txt").read_text(encoding="utf-8")
    parsed_jd = parse_jd_text(jd_text)

    rows: list[dict] = []
    for case in cases:
        resume_text = (FIXTURES / case["resume"]).read_text(encoding="utf-8")
        parsed_resume = parse_resume_text(resume_text)
        gold = [gold_map[key] for key in case["gold"] if key in gold_map]
        if len(gold) != len(case["gold"]):
            print(f"⚠️ {case['id']} 有 gold 切片未解析到（跳过该案例）")
            continue
        findings = analyze.sort_findings(
            analyze.rule_baseline_findings(parsed_resume, parsed_jd, "backend", resume_text, jd_text)
        )[:8]

        fixed = fixed_arm(resume_text, jd_text, parsed_resume, parsed_jd)
        agent = await agentic_arm(resume_text, jd_text, parsed_resume, parsed_jd, findings)

        row = {
            "id": case["id"],
            "resume": case["resume"],
            "gold": gold,
            "fixed": {**fixed, "gold_recall": ratio(fixed["chunk_ids"], gold)},
            "agentic": {
                **agent,
                "gold_recall": ratio(agent["retrieved"], gold),
                "citation_gold_rate": (len(set(agent["citations"]) & set(gold)) / len(agent["citations"]))
                if agent["citations"] else 0.0,
                "verified_rate": (agent["verify"]["verified"] / agent["verify"]["total"]) if agent["verify"]["total"] else 0.0,
            },
        }
        rows.append(row)
        print(
            f"{case['id']:9s} gold={len(gold)} | fixed recall={row['fixed']['gold_recall']:.2f} | "
            f"agentic recall={row['agentic']['gold_recall']:.2f} cited={len(agent['citations'])} "
            f"cite_gold={row['agentic']['citation_gold_rate']:.2f} rounds={agent['rounds']} "
            f"¥{agent['cost']:.3f} {agent['latency_ms']/1000:.0f}s [{agent['status']}]"
        )

    if not rows:
        print("没有可评测案例")
        return 1

    summary = {
        "cases": len(rows),
        "fixed_gold_recall": statistics.mean(r["fixed"]["gold_recall"] for r in rows),
        "agentic_gold_recall": statistics.mean(r["agentic"]["gold_recall"] for r in rows),
        # 是否至少引用到一条 gold（比"引用命中率"更公平：一条结论常有多条合理依据）
        "agentic_gold_cited_rate": statistics.mean(
            1.0 if (set(r["agentic"]["citations"]) & set(r["gold"])) else 0.0 for r in rows
        ),
        "fixed_has_no_citations": True,
        "agentic_citation_gold_rate": statistics.mean(r["agentic"]["citation_gold_rate"] for r in rows),
        "agentic_verified_rate": statistics.mean(r["agentic"]["verified_rate"] for r in rows),
        "agentic_rounds": statistics.mean(r["agentic"]["rounds"] for r in rows),
        "agentic_cost": statistics.mean(r["agentic"]["cost"] for r in rows),
        "agentic_latency_ms": statistics.mean(r["agentic"]["latency_ms"] for r in rows),
        "fixed_cannot_cite": True,
    }
    print("\n=== 汇总（%d 个案例）===" % summary["cases"])
    print(f"  检索覆盖 gold（固定检索）  : {summary['fixed_gold_recall']:.1%}")
    print(f"  检索覆盖 gold（Agent 多轮）: {summary['agentic_gold_recall']:.1%}")
    print(f"  引用命中 gold（Agent，宽口径）: {summary['agentic_citation_gold_rate']:.1%}")
    print(f"  至少引用到 1 条 gold（Agent）: {summary['agentic_gold_cited_rate']:.1%}")
    print(f"  引用逐字校验通过（Agent）  : {summary['agentic_verified_rate']:.1%}")
    print(f"  Agent 轮次 / 成本 / 延迟   : {summary['agentic_rounds']:.1f} 轮 / ¥{summary['agentic_cost']:.3f} / {summary['agentic_latency_ms']/1000:.0f}s")
    print("  说明：固定检索链路**无法产出 finding 级引用**（只有报告级 top-3 元数据），这是能力差异而非指标差异。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"eval_rag_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
