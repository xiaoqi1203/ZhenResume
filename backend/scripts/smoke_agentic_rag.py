"""Agentic RAG 冒烟与验收脚本：跑一次「知识库证据 Agent」，并**服务端复核**它给出的引用。

这一步先于产品接线，目的是证明两件事：
1. Agent 真的会**自主多轮检索**（trace 里能看到多轮自拟 query 与下钻）；
2. 它给出的引用**能被服务端逐字校验**（quote 确实存在于 chunk 文本中）。

用法（在 backend 目录下）：
    python -m scripts.smoke_agentic_rag                    # 默认用评分夹具里的一份简历
    python -m scripts.smoke_agentic_rag --file <path>      # 指定简历 txt
    python -m scripts.smoke_agentic_rag --jd <path>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.agentic import AgentInputs, BaseAgent, get_spec  # noqa: E402
from app.agentic.registry import SPECS  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import KnowledgeChunk  # noqa: E402
from app.services import analyze  # noqa: E402
from app.services.citation import attach_to_findings, verify_citations  # noqa: E402
from app.services.report_render import render_markdown  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
DEFAULT_RESUME = EVAL_DIR / "sort_fixtures" / "sort_B03.txt"
DEFAULT_JD = EVAL_DIR / "sort_fixtures" / "jd_backend.txt"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(DEFAULT_RESUME))
    parser.add_argument("--jd", default=str(DEFAULT_JD))
    parser.add_argument("--agent", default="knowledge_evidence")
    args = parser.parse_args()

    print("已注册 Agent:", sorted(SPECS))
    spec = get_spec(args.agent)
    resume_text = Path(args.file).read_text(encoding="utf-8")
    jd_text = Path(args.jd).read_text(encoding="utf-8")
    pr, pj = parse_resume_text(resume_text), parse_jd_text(jd_text)
    missing, _covered = analyze.missing_keywords(pr, pj, resume_text)

    findings = analyze.sort_findings(
        analyze.rule_baseline_findings(pr, pj, "backend", resume_text, jd_text)
    )[:6]
    inputs = AgentInputs(
        resume_text=resume_text,
        jd_text=jd_text,
        parsed_resume=pr,
        parsed_jd=pj,
        role_family="backend",
        extra={
            "missing_keywords": missing[:12],
            "findings": [{"index": i, "title": f.get("title"), "issue": (f.get("issue") or "")[:120]} for i, f in enumerate(findings)],
        },
    )

    run = await BaseAgent(spec).run(inputs)
    t = run.trace
    print(f"\n[{spec.title}] status={run.status}  notes={run.notes}")
    print(f"  模型往返 {t['model_calls']}｜工具 {t['tool_calls']}｜检索(请求/执行/拦截) {t.get('retrieval_requests')}/{t['retrieval_rounds']}/{t.get('retrieval_blocked')}｜自检 {t['self_checks']}")
    print(f"  tokens {run.tokens}｜成本 ¥{run.cost:.4f}｜延迟 {run.latency_ms}ms")
    print("  检索 query 序列:")
    for i, q in enumerate(t.get("retrieval_queries") or [], 1):
        print(f"    {i}. {q}")
    print("  工具序列:", t.get("tool_names"))

    citations = (run.output or {}).get("citations") or []
    raw_citations = [c if isinstance(c, dict) else c.model_dump() for c in citations]
    print(f"\n  Agent 产出引用 {len(raw_citations)} 条")
    with session_scope() as db:
        verified, vstats = verify_citations(db, raw_citations)
    print(f"  服务端校验: 总数 {vstats['total']}｜通过 {vstats['verified']}｜"
          f"chunk 不存在 {vstats['rejected_missing_chunk']}｜quote 定位失败 {vstats['rejected_quote_not_found']}｜重复 {vstats['rejected_duplicate']}")
    for c in verified:
        print(f"    OK {c['chunk_id'][:8]}《{c.get('doc_title') or ''}》 {c['quote'][:50]!r}")

    findings, astats = attach_to_findings(findings, verified)
    print(f"\n  挂载到 findings: {astats['attached']} 条（未匹配 {astats['unmatched']}）")
    for i, f in enumerate(findings):
        if f.get("citations"):
            print(f"    [{i}] {f.get('title','')[:34]} <- {len(f['citations'])} 条依据")

    md = render_markdown(
        {
            "report_id": "smoke", "resume_title": Path(args.file).name, "jd_title": pj.title,
            "created_at": "", "scores": {"overall": 0}, "summary": "",
            "findings": findings, "knowledge_refs": [], "missing_keywords": missing, "action_items": [],
        }
    )
    excerpt = [ln for ln in md.splitlines() if "知识库依据" in ln or ln.startswith("  - 《")]
    print("\n  渲染片段:")
    for ln in excerpt[:8]:
        print("    " + ln)
    reasoning = (run.output or {}).get("reasoning")
    if reasoning:
        print(f"\n  Agent 自述: {str(reasoning)[:300]}")

    ok = vstats["verified"] > 0 and astats["attached"] > 0
    print("\n结论:", "OK Agentic RAG 闭环成立（多轮检索 -> 可校验引用 -> 挂载入报告）" if ok else "FAIL 引用未能挂载")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
