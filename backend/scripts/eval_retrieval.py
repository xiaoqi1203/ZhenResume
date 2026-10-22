"""检索器对照评测：单 query / scope 融合 / 重排 / 多视角 四类方案的裁决台。

## 为什么单独一个脚本

`scripts/eval_rag.py` 评的是**产品级两臂**（fixed 链路 vs Agent 多轮），跑一次要 4 分钟、¥0.8。
本脚本只测**检索器本身**，零 LLM 调用（只有 embedding），秒级完成、可反复跑 ——
用来回答"改检索器到底有没有用"，是 `eval_rag` 的上游对照实验。

## 四个口径

| 口径 | 含义 |
|---|---|
| `recall@k` | 单次检索的 top-k 命中 gold 比例 |
| `scope_coverage@k` | top-k 覆盖了几个 scope（满分 4） |
| `bm25_*` / `mmr_*` | 重排臂（零依赖：BM25 / MMR） |
| `multi_persp` / `persp_only` | 多视角 query 融合臂 |

## ⚠️ 关于 gold 标注的已知局限（读之前必看）

`CASES` 里的 gold **只标了 5 篇自撰文档**（能力模型 / 表达结构 / ATS 机制），
而扩库后（2026-10-21）知识库有 **19 篇 / 43 切片**，其中 8 篇外部权威语料
（O*NET 四篇、McMaster、Dartmouth、SocialTalent、EEOC）**完全不在 gold 里**。

后果：
1. **recall 的天花板被锁在旧文档上** —— 即使检索器把 O*NET 的切片排到第一，
   也不计入 recall（它不在 gold 里）；
2. **任何"增加覆盖面"的改动都会被惩罚** —— 因为 O*NET 切片占了 top-k 名额却不计分。
   这正是 scope 融合（30.8%）与多视角（89.2% 无提升）数字难看的主因之一。

**这不是 bug，是评测集没跟上库的增长。** 正确的下一步是**扩 gold 标注**
（对每份简历，把 O*NET / McMaster / Dartmouth 里"真正能支撑该结论"的切片也标进去），
而不是继续调检索算法。本脚本把这件事显式记录下来，避免后来者误判。

## 用法（在 backend 目录下）

```bash
python -m scripts.eval_retrieval              # 10 个 case，秒级
python -m scripts.eval_retrieval --top-k 8    # 换 k 看稳定性
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

FIXTURES = BACKEND_DIR / "eval" / "sort_fixtures"
OUT_DIR = BACKEND_DIR / "eval" / "runs"

# 夹具：全部 32 份（A/B/C/D 各 8 份，设计覆盖度从 ~100% 降到 ~19%）。
#
# 为什么从 10 例扩到 32 例：10 例的均值方差太大（P2 实测同一指标三次运行 55%/58%/42%），
# 效应量会被噪声淹没。32 例能把标准误压到约 1/√3.2 ≈ 56%。
#
# `gold` 字段保留但**不再用于 v2**（v2 从规则层自动推导 topic，见 gold_v2.py）。
# v1 仍需它（按文档手写的固定 gold），保留以便双口径对照。
def _all_fixtures() -> list[dict]:
    """扫描夹具目录，返回全部 case 定义。"""
    out: list[dict] = []
    for path in sorted(FIXTURES.glob("sort_*.txt")):
        stem = path.stem  # sort_A00
        group = stem.split("_")[1][0]
        out.append(
            {
                "id": f"rag_{stem.split('_')[1]}",
                "resume": path.name,
                "group": group,
                # v1 gold：沿用旧口径（所有 case 同一组自撰文档），仅用于对照
                "gold": [
                    ("后端工程师能力模型与量化标准", 0),
                    ("后端工程师能力模型与量化标准", 1),
                    ("经历要点的黄金表达结构", 0),
                ],
            }
        )
    return out


CASES: list[dict] = _all_fixtures()


def resolve_gold() -> dict[tuple[str, int], str]:
    """(文档标题, 切片序号) -> chunk_id。与 eval_rag.py 同实现。"""
    mapping: dict[tuple[str, int], str] = {}
    with session_scope() as db:
        for chunk in db.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.created_at)).scalars().all():
            doc = db.get(KnowledgeDoc, chunk.doc_id)
            if doc is None:
                continue
            index = sum(1 for key in mapping if key[0] == doc.title)
            mapping[(doc.title, index)] = chunk.id
    return mapping


def recall_of(hits: list[dict], gold: list[str]) -> float:
    if not gold:
        return 0.0
    got = {h.get("chunk_id") for h in hits}
    return len(got & set(gold)) / len(set(gold))


def gold_v1_ids(case: dict, gold_map: dict[tuple[str, int], str]) -> list[str]:
    """v1：按文档手写的固定 gold（只覆盖 5 篇自撰规范）。"""
    return [gold_map[key] for key in case["gold"] if key in gold_map]


def gold_v2_ids(case: dict, gold_map: dict[tuple[str, int], str], findings: list[dict] | None = None) -> list[str]:
    """v2：从「该简历实际触发的规则层结论」推出「支撑切片」。

    见 `scripts/gold_v2.py` —— 每条映射都带理由，可人工复核。

    `findings`：该夹具的规则层发现项。传了就用**自动推导**（`derive_topics_from_findings`），
    这样对任意夹具都能工作（32 例全用得上）；没传则回退到手工标注的 10 例表。
    """
    from scripts.gold_v2 import derive_topics_from_findings, gold_for_case, gold_for_topics

    if findings is not None:
        keys = gold_for_topics(derive_topics_from_findings(findings))
    else:
        keys = gold_for_case(case["id"])
    return [gold_map[key] for key in keys if key in gold_map]


def scope_coverage(hits: list[dict]) -> int:
    return len({h.get("scope") or "" for h in hits if h.get("scope")})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()
    k = args.top_k

    gold_map = resolve_gold()
    jd_text = (FIXTURES / "jd_backend.txt").read_text(encoding="utf-8")
    parsed_jd = parse_jd_text(jd_text)

    rows: list[dict] = []
    for case in CASES:
        resume_text = (FIXTURES / case["resume"]).read_text(encoding="utf-8")
        parsed_resume = parse_resume_text(resume_text)

        # 该夹具的规则层发现项 —— v2 gold 从这里自动推导（对任意夹具都能工作）
        findings = analyze.rule_baseline_findings(
            parsed_resume, parsed_jd, "backend", resume_text, jd_text
        )

        # ★ 两套 gold 同时评：v1（固定 5 篇自撰规范）与 v2（从结论推出依据，含外部语料）
        gold = gold_v1_ids(case, gold_map)
        if len(gold) != len(case["gold"]):
            print(f"⚠️ {case['id']} gold v1 未解析完整，跳过")
            continue
        gold2 = gold_v2_ids(case, gold_map, findings)
        if not gold2:
            # 该夹具没有任何"知识库有依据"的结论 → 不计入 v2 均值（避免用 0 拉低分数）
            continue

        # 产品现状：JD 宽 query，单次全局排序
        wide = rag.build_query(parsed_jd.model_dump(), parsed_resume.model_dump(), raw_text=resume_text)
        t0 = time.perf_counter()
        with session_scope() as db:
            baseline = rag.search(db, wide, top_k=k, role_family="backend")
        t_base = int((time.perf_counter() - t0) * 1000)

        # 升级版：同一 query 走 scope 融合（Agent 的窄 query 也受益于同一机制）
        narrow = f"{parsed_jd.title or ''} 能力模型 量化标准"
        t0 = time.perf_counter()
        with session_scope() as db:
            scoped_wide = rag.search_scoped(db, wide, top_k=k, role_family="backend")
            scoped_narrow = rag.search_scoped(db, narrow, top_k=k, role_family="backend")
            # 保底席位对照（scope_quota=1 是"强制每类各一席"的旧方案）
            quota_wide = rag.search_scoped(db, wide, top_k=k, role_family="backend", scope_quota=1)
        t_scoped = int((time.perf_counter() - t0) * 1000)

        # ★ 重排对照臂（零依赖：BM25 / MMR），每臂单独计时
        arms: dict[str, dict] = {}
        for label, kwargs in (
            ("bm25_0.5", {"rerank": True, "rerank_weight": 0.5}),
            ("bm25_0.7", {"rerank": True, "rerank_weight": 0.7}),
            ("mmr_0.7", {"mmr_lambda": 0.7}),
            ("bm25+mmr", {"rerank": True, "rerank_weight": 0.5, "mmr_lambda": 0.7}),
        ):
            t1 = time.perf_counter()
            with session_scope() as db:
                got = rag.search(db, wide, top_k=k, role_family="backend", **kwargs)
            arms[label] = {
                "recall": recall_of(got, gold),
                "recall_v2": recall_of(got, gold2),
                "scope_coverage": scope_coverage(got),
                "latency_ms": int((time.perf_counter() - t1) * 1000),
            }

        # ★ 多视角融合臂（每条 query 问不同的问题，RRF 融合）
        t1 = time.perf_counter()
        with session_scope() as db:
            multi_wide = rag.search_multi(
                db, [wide] + rag.perspective_queries(parsed_jd.title or ""),
                top_k=k, role_family="backend",
            )
            multi_only = rag.search_multi(
                db, rag.perspective_queries(parsed_jd.title or ""),
                top_k=k, role_family="backend",
            )
        arms["multi_persp"] = {
            "recall": recall_of(multi_wide, gold),
            "recall_v2": recall_of(multi_wide, gold2),
            "scope_coverage": scope_coverage(multi_wide),
            "latency_ms": int((time.perf_counter() - t1) * 1000),
        }
        arms["persp_only"] = {
            "recall": recall_of(multi_only, gold),
            "recall_v2": recall_of(multi_only, gold2),
            "scope_coverage": scope_coverage(multi_only),
            "latency_ms": 0,
        }

        row = {
            "id": case["id"],
            "gold": gold,
            "gold_v2": gold2,
            "baseline": {
                "query": "jd_wide",
                "recall": recall_of(baseline, gold),
                "recall_v2": recall_of(baseline, gold2),
                "scope_coverage": scope_coverage(baseline),
                "scopes": sorted({h.get("scope") for h in baseline if h.get("scope")}),
                "latency_ms": t_base,
            },
            "scoped_wide": {
                "query": "jd_wide",
                "recall": recall_of(scoped_wide, gold),
                "scope_coverage": scope_coverage(scoped_wide),
                "scopes": sorted({h.get("scope") for h in scoped_wide if h.get("scope")}),
                "latency_ms": t_scoped,
            },
            "scoped_narrow": {
                "query": "agent_narrow",
                "recall": recall_of(scoped_narrow, gold),
                "scope_coverage": scope_coverage(scoped_narrow),
                "scopes": sorted({h.get("scope") for h in scoped_narrow if h.get("scope")}),
            },
            # 旧方案对照：每 scope 保底 1 席（实测被否，保留数字以免重蹈覆辙）
            "quota_wide": {
                "query": "jd_wide",
                "recall": recall_of(quota_wide, gold),
                "scope_coverage": scope_coverage(quota_wide),
            },
            "arms": arms,
            # ★ 各臂在 v2 gold 下的表现（v2 含外部语料，更能反映"是否引到点子上"）
            "arms_v2": {
                label: {"recall": v["recall_v2"]}
                for label, v in arms.items()
            },
        }
        rows.append(row)
        arm_txt = " ".join(f"{k}={v['recall']:.2f}" for k, v in arms.items())
        print(
            f"{case['id']:9s} gold_v1={len(gold)} gold_v2={len(gold2)} | "
            f"base {row['baseline']['recall']:.2f} (v2 {row['baseline']['recall_v2']:.2f}) | "
            f"scoped {row['scoped_wide']['recall']:.2f} | quota {row['quota_wide']['recall']:.2f} | {arm_txt}"
        )

    if not rows:
        print("没有可评测案例")
        return 1

    def mean(key: str, arm: str) -> float:
        return round(statistics.mean(r[arm][key] for r in rows), 4)

    summary = {
        "cases": len(rows),
        "top_k": k,
        "baseline_recall": mean("recall", "baseline"),
        "scoped_wide_recall": mean("recall", "scoped_wide"),
        "scoped_narrow_recall": mean("recall", "scoped_narrow"),
        "quota_wide_recall": mean("recall", "quota_wide"),
        "baseline_scope_coverage": mean("scope_coverage", "baseline"),
        "scoped_scope_coverage": mean("scope_coverage", "scoped_wide"),
        "quota_scope_coverage": mean("scope_coverage", "quota_wide"),
        "baseline_latency_ms": mean("latency_ms", "baseline"),
        "scoped_latency_ms": mean("latency_ms", "scoped_wide"),
        # ★ v2 gold（从结论推出依据，含外部语料）下的基线
        "baseline_recall_v2": round(
            statistics.mean(r["baseline"]["recall_v2"] for r in rows), 4
        ),
        "arms": {
            label: {
                "recall": round(statistics.mean(r["arms"][label]["recall"] for r in rows), 4),
                "recall_v2": round(
                    statistics.mean(r["arms"][label]["recall_v2"] for r in rows), 4
                ),
                "scope_coverage": round(
                    statistics.mean(r["arms"][label]["scope_coverage"] for r in rows), 2
                ),
                "latency_ms": round(
                    statistics.mean(r["arms"][label]["latency_ms"] for r in rows), 0
                ),
            }
            for label in rows[0]["arms"]
        },
    }
    print("\n=== 汇总（gold v1：固定 5 篇自撰规范）===")
    print(f"  recall@{k}（JD 宽 query，单次全局排序，现状）: {summary['baseline_recall']:.1%}")
    print(f"  recall@{k}（JD 宽 query，scope 融合 RRF）     : {summary['scoped_wide_recall']:.1%}"
          f"  Δ {summary['scoped_wide_recall'] - summary['baseline_recall']:+.1%}")
    print(f"  recall@{k}（JD 宽 query，融合+每 scope 保底）  : {summary['quota_wide_recall']:.1%}"
          f"  Δ {summary['quota_wide_recall'] - summary['baseline_recall']:+.1%}  ← 旧方案，已被否")
    print(f"  recall@{k}（Agent 窄 query，scope 融合）      : {summary['scoped_narrow_recall']:.1%}")
    print(f"  scope 覆盖数：基线 {summary['baseline_scope_coverage']:.2f} → "
          f"融合 {summary['scoped_scope_coverage']:.2f} → 保底 {summary['quota_scope_coverage']:.2f}（满分 4）")

    print("\n=== ★ gold v2（从规则层结论推出依据，含 O*NET / McMaster / Dartmouth）===")
    print(f"  基线 recall@{k} : {summary['baseline_recall_v2']:.1%}"
          f"（v1 下为 {summary['baseline_recall']:.1%}）")

    print("\n=== 对照臂（v1 / v2 双口径）===")
    for label, st in summary["arms"].items():
        d1 = st["recall"] - summary["baseline_recall"]
        d2 = st["recall_v2"] - summary["baseline_recall_v2"]
        m1 = "✅" if d1 > 0.005 else ("❌" if d1 < -0.005 else "＝")
        m2 = "✅" if d2 > 0.005 else ("❌" if d2 < -0.005 else "＝")
        print(f"  {label:11s} v1={st['recall']:.1%} (Δ{d1:+.1%}){m1}  "
              f"v2={st['recall_v2']:.1%} (Δ{d2:+.1%}){m2}  "
              f"scope={st['scope_coverage']:.2f} {st['latency_ms']:.0f}ms")

    out = args.json_out or str(OUT_DIR / f"eval_retrieval_{time.strftime('%Y%m%d-%H%M%S')}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")

    # 合入判据：任一臂在**任一 gold 口径**下显著优于基线（>1pp）
    improved = [
        label for label, v in summary["arms"].items()
        if v["recall"] > summary["baseline_recall"] + 0.01
        or v["recall_v2"] > summary["baseline_recall_v2"] + 0.01
    ]
    if improved:
        print(f"\n✓ 有臂显著优于基线：{', '.join(improved)}")
        return 0
    print("\n✗ 没有臂显著优于基线 → 检索算法已不是瓶颈，问题在评测集或知识库覆盖")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
