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
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
DEFAULT_RESUME = EVAL_DIR / "sort_fixtures" / "sort_B03.txt"
DEFAULT_JD = EVAL_DIR / "sort_fixtures" / "jd_backend.txt"


def verify_citations(citations: list[dict]) -> dict:
    """服务端复核：chunk 存在 + quote 能在其文本中**逐字**定位。"""
    stats = {"total": 0, "chunk_found": 0, "quote_verified": 0, "details": []}
    with session_scope() as db:
        for c in citations or []:
            stats["total"] += 1
            chunk_id = str(c.get("chunk_id") or "")
            quote = str(c.get("quote") or "").strip()
            chunk = db.get(KnowledgeChunk, chunk_id) if chunk_id else None
            found = chunk is not None
            ok = bool(found and quote and quote in (chunk.chunk_text or ""))
            stats["chunk_found"] += int(found)
            stats["quote_verified"] += int(ok)
            stats["details"].append(
                {
                    "chunk_id": chunk_id,
                    "supports": str(c.get("supports") or "")[:40],
                    "found": found,
                    "quote_ok": ok,
                    "quote": quote[:60],
                }
            )
    return stats


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

    inputs = AgentInputs(
        resume_text=resume_text,
        jd_text=jd_text,
        parsed_resume=pr,
        parsed_jd=pj,
        role_family="backend",
        extra={"missing_keywords": missing[:12]},
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
    print(f"\n  产出的引用 {len(citations)} 条")
    stats = verify_citations([c if isinstance(c, dict) else c.model_dump() for c in citations])
    print(f"  服务端复核: chunk 存在 {stats['chunk_found']}/{stats['total']}｜quote 逐字可定位 {stats['quote_verified']}/{stats['total']}")
    for d in stats["details"][:10]:
        mark = "✓" if d["quote_ok"] else "✗"
        print(f"    {mark} {d['chunk_id'][:8]} 支撑={d['supports']!r} quote={d['quote']!r}")
    reasoning = (run.output or {}).get("reasoning")
    if reasoning:
        print(f"\n  Agent 自述: {str(reasoning)[:300]}")

    ok = stats["total"] > 0 and stats["quote_verified"] >= max(1, stats["total"] // 2)
    print("\n结论:", "✓ Agentic RAG 闭环成立（多轮检索 + 可校验引用）" if ok else "✗ 引用质量不足，需调整提示词/工具")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
