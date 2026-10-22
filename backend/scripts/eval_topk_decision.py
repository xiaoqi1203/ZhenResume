"""`top_k` 产品决策评估：候选池大小如何影响**真正注入 prompt 的前 3 条**质量。

## 为什么这个脚本与 `eval_retrieval.py` 不同

`eval_retrieval.py` 评的是 `recall@k` —— top-k 里有没有 gold。但产品链路**并不把 top-k 全塞进
prompt**：`knowledge_digest(limit=3)` 只取**前 3 条**（`agents/base.py:63`）。

所以真正该问的问题是：

> **`top_k` 从 5 提到 10，注入 prompt 的那 3 条会变好吗？**

如果前 3 条的质量不变，那改 `top_k` 就是纯浪费（虽然成本影响很小）；
如果前 3 条显著变好，那才是该改的理由。

## 三个指标

| 指标 | 含义 |
|---|---|
| `top3_recall` | 注入 prompt 的前 3 条里，命中 v2 gold 的比例 |
| `top3_scope` | 前 3 条覆盖了几个 scope（用户看到几类依据） |
| `prompt_chars` | 注入 prompt 的字符数（**验证成本不变**） |

## 用法

```bash
python -m scripts.eval_topk_decision           # 对比 top_k = 3/5/8/10/12
```
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import KnowledgeChunk, KnowledgeDoc  # noqa: E402
from app.services import analyze, rag  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from scripts.eval_retrieval import CASES, gold_v2_ids, resolve_gold  # noqa: E402

FIXTURES = BACKEND_DIR / "eval" / "sort_fixtures"
OUT_DIR = BACKEND_DIR / "eval" / "runs"

# 产品链路实际注入 prompt 的条数（`agents/base.py:knowledge_digest(limit=3)`）
PROMPT_LIMIT = 3
# 每条注入的最大字符（同上，max_chars=300）
PROMPT_CHARS_PER_HIT = 300


def prompt_chars(hits: list[dict], limit: int = PROMPT_LIMIT) -> int:
    """模拟 `knowledge_digest` 实际拼进 prompt 的字符数。"""
    total = 0
    for h in hits[:limit]:
        title = h.get("title") or ""
        text = str(h.get("text") or "")[:PROMPT_CHARS_PER_HIT]
        total += len(f"- [{title}] {text}") + 1  # +1 换行
    return total


def recall_of(hits: list[dict], gold: list[str], limit: int = PROMPT_LIMIT) -> float:
    """只算**前 limit 条**的 gold 命中率（这才是真正进了 prompt 的部分）。"""
    if not gold:
        return 0.0
    got = {h.get("chunk_id") for h in hits[:limit]}
    return len(got & set(gold)) / len(set(gold))


def scope_coverage(hits: list[dict], limit: int = PROMPT_LIMIT) -> int:
    return len({h.get("scope") or "" for h in hits[:limit] if h.get("scope")})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ks", type=str, default="3,5,8,10,12", help="逗号分隔的 top_k 候选")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    gold_map = resolve_gold()
    jd_text = (FIXTURES / "jd_backend.txt").read_text(encoding="utf-8")
    parsed_jd = parse_jd_text(jd_text)

    # 预取每例的 query 与 gold（全部 32 份夹具；gold 从规则层自动推导）
    prepared: list[tuple[dict, str, list[str]]] = []
    for case in CASES:
        resume_text = (FIXTURES / case["resume"]).read_text(encoding="utf-8")
        parsed_resume = parse_resume_text(resume_text)
        findings = analyze.rule_baseline_findings(
            parsed_resume, parsed_jd, "backend", resume_text, jd_text
        )
        wide = rag.build_query(parsed_jd.model_dump(), parsed_resume.model_dump())
        gold2 = gold_v2_ids(case, gold_map, findings)
        if not gold2:
            continue
        prepared.append((case, wide, gold2))

    rows: list[dict] = []
    for k in ks:
        per_case: list[dict] = []
        t0 = time.perf_counter()
        for case, wide, gold2 in prepared:
            with session_scope() as db:
                hits = rag.search(db, wide, top_k=k, role_family="backend")
                # ★ 关键对照：BM25 重排是否改善**前 3 条**（产品真正注入 prompt 的部分）
                hits_bm25 = rag.search(
                    db, wide, top_k=k, role_family="backend",
                    rerank=True, rerank_weight=0.7,
                )
            per_case.append(
                {
                    "id": case["id"],
                    "top3_recall": recall_of(hits, gold2),
                    "top3_recall_bm25": recall_of(hits_bm25, gold2),
                    "top3_scope": scope_coverage(hits),
                    "prompt_chars": prompt_chars(hits),
                    "k_returned": len(hits),
                }
            )
        elapsed = int((time.perf_counter() - t0) * 1000)
        rows.append(
            {
                "top_k": k,
                "top3_recall": round(statistics.mean(c["top3_recall"] for c in per_case), 4),
                "top3_recall_bm25": round(
                    statistics.mean(c["top3_recall_bm25"] for c in per_case), 4
                ),
                "top3_scope": round(statistics.mean(c["top3_scope"] for c in per_case), 2),
                "prompt_chars": round(statistics.mean(c["prompt_chars"] for c in per_case), 0),
                "avg_returned": round(statistics.mean(c["k_returned"] for c in per_case), 1),
                "latency_ms": elapsed,
                "cases": per_case,
            }
        )

    print(f"=== top_k 对**注入 prompt 的前 3 条**的影响（v2 gold，{len(prepared)} 例）===")
    print(
        f"{'top_k':>6} {'前3条recall':>12} {'BM25重排':>10} {'前3条scope':>11} "
        f"{'prompt字符':>11} {'返回条数':>9}"
    )
    base = rows[0]["top3_recall"] if rows else 0
    for r in rows:
        delta = r["top3_recall"] - base
        mark = "✅" if delta > 0.02 else ("❌" if delta < -0.02 else "＝")
        d_bm25 = r["top3_recall_bm25"] - r["top3_recall"]
        mark_b = "✅" if d_bm25 > 0.02 else ("❌" if d_bm25 < -0.02 else "＝")
        print(
            f"{r['top_k']:>6} {r['top3_recall']:>11.1%} "
            f"{r['top3_recall_bm25']:>9.1%}({d_bm25:+.1%}){mark_b} "
            f"{r['top3_scope']:>11.2f} {r['prompt_chars']:>11.0f} {r['avg_returned']:>9.1f}  "
            f"(Δ{delta:+.1%} {mark})"
        )

    print("\n说明：")
    print("  · `前3条recall` = 产品真正注入 prompt 的 3 条里命中 gold 的比例")
    print("  · `BM25重排` 列 = 同一 top_k 下开启 BM25 重排后的前 3 条 recall")
    print("  · 若 prompt_chars 不随 top_k 变化 → **改 top_k 不影响 token 成本**")
    print("  · 若 BM25 列无提升 → 重排对产品零收益（即使 recall@k 有提升）")

    out = args.json_out or str(OUT_DIR / f"eval_topk_{time.strftime('%Y%m%d-%H%M%S')}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")

    # 判据 1：更大的 top_k 是否让前 3 条变好
    best = max(rows, key=lambda r: r["top3_recall"])
    k_helps = best["top3_recall"] > base + 0.02
    # 判据 2：BM25 是否让前 3 条变好
    bm25_best = max(rows, key=lambda r: r["top3_recall_bm25"])
    bm25_helps = bm25_best["top3_recall_bm25"] > base + 0.02

    if k_helps:
        print(f"\n✓ top_k={best['top_k']} 让前 3 条 recall 提升 "
              f"{best['top3_recall'] - base:+.1%}，且 prompt 成本不变 → 值得改")
    if bm25_helps:
        print(f"✓ BM25 重排（k={bm25_best['top_k']}）让前 3 条提升 "
              f"{bm25_best['top3_recall_bm25'] - base:+.1%} → 值得启用")
    if k_helps or bm25_helps:
        return 0
    print("\n✗ 改 top_k 与 BM25 重排都没有改善前 3 条 → 对产品零收益，都不该改")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
