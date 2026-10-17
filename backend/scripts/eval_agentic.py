"""P-1/P4：语义判定与分层加权的评测脚本。

对 `eval/fixtures/` 下的夹具简历逐份执行：
    规则层缺失清单 → 语义判定（LLM）→ locate_quote 硬校验 → 覆盖率合并 → 打分

输出四类数字，全部可复现（`--engine single` 走单次调用；`--engine agentic` 预留）：

| 指标 | 说明 |
|---|---|
| 假差距 recall | 期望为 false_gap 的关键词中，被判出且证据校验通过的比例 |
| 假差距 precision | 判为 false_gap 的条目中，确实在期望列表里的比例 |
| 证据校验通过率 | 判为 false_gap 的条目中，`locate_quote` 能在原文定位的比例 |
| 误判率 | 期望为 real_gap 却被判成 false_gap 的比例（越低越好） |

以及 ATS/overall 在三种口径下的对比：`v1 等权` / `v2 分层` / `v2 分层+语义`。

用法（在 backend 目录下）：
    python -m scripts.eval_agentic --engine single --limit 30      # 调 LLM
    python -m scripts.eval_agentic --engine rule   --limit 30      # 不调 LLM，只看规则层基线
    python -m scripts.eval_agentic --engine single --dry-run       # 只校验夹具自洽性

⚠️ 会调用真实 LLM（每份约 1 次调用）。默认 `--engine rule`，避免误花钱。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import textwrap
import time
from pathlib import Path

# Windows 控制台默认 GBK，会把 ¥ / ✅ 之类的字符打成异常 —— 统一强制 UTF-8。
# 用 reconfigure 而不是 io.TextIOWrapper：后者接管 buffer 所有权，
# 包装对象被回收时会把 stdout 关掉（实测报 "I/O operation on closed file"）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.schemas import ParsedJd, ParsedResume  # noqa: E402
from app.services import analyze  # noqa: E402
from app.services.evidence import locate_quote  # noqa: E402
from app.services.llm import get_llm  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
FIX_DIR = EVAL_DIR / "fixtures"

SYSTEM_PROMPT = """你是简历语义判定模块。你的唯一职责：对 JD 要求中"字面缺失"的关键词，
判断它是【真差距】还是【表述差异（能力其实具备，只是用词不同）】。

铁律：
1) 判定为"表述差异"时，必须给出**简历原文中的连续片段**作为证据，逐字复制，
   不得改写、不得拼接、不得增删标点、不得跨句拼接；
2) 找不到逐字证据就必须判为"真差距"，绝不编造；
3) 只输出 JSON。"""

USER_TEMPLATE = """【JD 要求（字面缺失清单）】{missing}

【简历原文】
{resume}

输出 JSON：
{{"judgments":[{{"keyword":"<缺失关键词>","verdict":"false_gap|real_gap",
"resume_quote":"<逐字原文片段，real_gap 留空字符串>","reason":"<一句话>"}}]}}"""


def load_fixtures(limit: int) -> list[dict]:
    expected = {
        json.loads(line)["record_id"]: json.loads(line)
        for line in (EVAL_DIR / "fixtures_expected.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    jd_raw = (FIX_DIR / "jd_backend.txt").read_text(encoding="utf-8")
    out = []
    for rid in sorted(expected)[:limit]:
        rec = expected[rid]
        out.append(
            {
                "record_id": rid,
                "kind": rec["kind"],
                "expected_false_gaps": rec["expected_false_gaps"],
                "expected_real_gaps": rec["expected_real_gaps"],
                "raw_text": (FIX_DIR / rec["file"]).read_text(encoding="utf-8"),
                "jd_raw": jd_raw,
            }
        )
    return out


def rule_baseline(rec: dict) -> dict:
    pr = parse_resume_text(rec["raw_text"])
    pj = parse_jd_text(rec["jd_raw"])
    missing, covered = analyze.missing_keywords(pr, pj, rec["raw_text"])
    return {"pr": pr, "pj": pj, "missing": missing, "covered": covered}


async def judge_one(rec: dict, base: dict, llm, cache_dir: Path) -> dict:
    """单次语义判定。带磁盘缓存：同一夹具 + 同一 missing 清单直接复用，避免重复付费。"""
    missing = base["missing"]
    if not missing:
        return {"judgments": [], "llm": None, "wall_ms": 0}

    cache_file = cache_dir / f"{rec['record_id']}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("missing") == missing:
            cached["from_cache"] = True
            return cached

    user = USER_TEMPLATE.format(missing="、".join(missing), resume=rec["raw_text"])
    started = time.perf_counter()
    res = await llm.complete_json(
        system=SYSTEM_PROMPT,
        user=user,
        mock_factory=lambda: {"judgments": []},
        agent="semantic_eval",
    )
    payload = {
        "record_id": rec["record_id"],
        "missing": missing,
        "judgments": res.data.get("judgments") or [],
        "llm": {
            "mode": res.mode,
            "model": res.model,
            "latency_ms": res.latency_ms,
            "tokens": res.tokens_in + res.tokens_out,
            "cost": res.cost,
        },
        "wall_ms": int((time.perf_counter() - started) * 1000),
        "from_cache": False,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def verify(rec: dict, judgments: list[dict], base: dict) -> tuple[list[tuple[str, str]], dict]:
    """按说明书 §6.2 的硬校验规则过滤，返回 (通过列表, 统计)。"""
    required = {k.lower() for k in base["pj"].all_skills()}
    missing_lower = {m.lower() for m in base["missing"]}
    covered_lower = {c.lower() for c in base["covered"]}
    seen: set[str] = set()
    passed: list[tuple[str, str]] = []
    stat = {"judged_false": 0, "located_fail": 0, "not_required": 0, "duplicate": 0, "already_covered": 0}
    for j in judgments:
        if str(j.get("verdict")) != "false_gap":
            continue
        stat["judged_false"] += 1
        kw = str(j.get("keyword") or "").strip()
        quote = str(j.get("resume_quote") or "").strip()
        if not kw or not quote:
            stat["located_fail"] += 1
            continue
        k = kw.lower()
        if k not in required:
            stat["not_required"] += 1
            continue
        if k in seen or k in covered_lower or k not in missing_lower:
            stat["duplicate"] += 1
            continue
        if locate_quote(rec["raw_text"], quote) is None:
            stat["located_fail"] += 1
            continue
        seen.add(k)
        passed.append((kw, quote))
    return passed, stat


def _local_weighted_coverage(missing, covered, pj, semantic):
    """分层加权覆盖率的**本地等价实现**。

    P0 阶段会把 `_weighted_coverage` 落地到 `services/analyze.py`（说明书 §4.1/§10.3）；
    在此之前评估脚本自带一份，保证 P-1 就能拿到数字。
    生产版落地后本函数自动让位（见 `scores_for` 里的判定）。
    """
    def tier(kw: str) -> str:
        k = (kw or "").lower()
        if any(k == s.lower() for s in pj.skills_core):
            return "core"
        if any(k == s.lower() for s in pj.skills_bonus):
            return "bonus"
        return "keyword"

    def weight(kw: str) -> float:
        return {
            "core": getattr(settings, "jd_tier_weight_core", 1.0),
            "bonus": getattr(settings, "jd_tier_weight_bonus", 0.6),
            "keyword": getattr(settings, "jd_tier_weight_keyword", 0.3),
        }.get(tier(kw), 1.0)

    verified = {kw.lower() for kw, _ in semantic}
    hit_w = sum(weight(kw) for kw in covered)
    miss_w = 0.0
    for kw in missing:
        if kw.lower() in verified:
            hit_w += weight(kw)
        else:
            miss_w += weight(kw)
    total_w = hit_w + miss_w
    if total_w == 0:
        return 0.85
    return hit_w / total_w


def _weighted_coverage(missing, covered, pj, semantic):
    """优先用生产实现；未落地时用本地等价实现。"""
    fn = getattr(analyze, "_weighted_coverage", None)
    if fn is not None:
        cov, _hit, _total = fn(missing, covered, pj, semantic)
        return cov
    return _local_weighted_coverage(missing, covered, pj, semantic)


def scores_for(base: dict, rec: dict, semantic: list[tuple[str, str]], tiered: bool) -> dict:
    """按口径算分。

    - `tiered=False`：**v1 等权**，无语义（现状基线）；
    - `tiered=True` 且 `semantic` 有值：**v2 分层 + 语义**；
    - `tiered=True` 且 `semantic` 为空：**v2 仅分层**（用于拆解分层单独的贡献）。
    """
    pr, pj = base["pr"], base["pj"]
    sem = semantic or []
    if tiered:
        cov = _weighted_coverage(base["missing"], base["covered"], pj, sem)
    else:
        hit = len(base["covered"])
        total = len(base["missing"]) + len(base["covered"])
        cov = hit / total if total else 0.85
    scored = [f for f in analyze.rule_baseline_findings(pr, pj, "general", rec["raw_text"], rec["jd_raw"])
              if analyze.is_rule_origin(f)]
    verified_kw = {kw.lower() for kw, _ in sem}
    scored = [f for f in scored if not _is_missing_keyword_finding(f, verified_kw)]
    penalty_ats = analyze._penalty(scored, "ats", red=5, yellow=2, cap=2)
    return {
        "coverage": cov,
        "ats": analyze._clamp(45 + cov * 55 - penalty_ats),
    }


def _is_missing_keyword_finding(f: dict, verified_kw: set[str]) -> bool:
    """该 finding 是否是"某个已被语义校验覆盖的关键词缺失"条目。"""
    if not verified_kw:
        return False
    if f.get("category") != "ats":
        return False
    title = str(f.get("title") or "")
    if "关键词" not in title or "缺失" not in title:
        return False
    return any(kw in title.lower() for kw in verified_kw)


async def run(limit: int, engine: str, dry_run: bool) -> None:
    fixtures = load_fixtures(limit)
    if dry_run:
        for rec in fixtures:
            base = rule_baseline(rec)
            hit = [k for k in rec["expected_false_gaps"] if k in base["missing"]]
            print(f"{rec['record_id']}  kind={rec['kind']:6s} 缺失={len(base['missing']):2d} "
                  f"期望假差距={rec['expected_false_gaps']} 其中被规则判为缺失={hit}")
        return

    llm = get_llm() if engine == "single" else None
    cache_dir = EVAL_DIR / "runs" / "cache"
    rows: list[dict] = []
    total_cost = 0.0
    cache_hits = 0
    t0 = time.perf_counter()

    for rec in fixtures:
        base = rule_baseline(rec)
        semantic: list[tuple[str, str]] = []
        judged_false: list[str] = []
        stat: dict = {}
        if engine == "single":
            out = await judge_one(rec, base, llm, cache_dir)
            if out.get("from_cache"):
                cache_hits += 1
            total_cost += (out.get("llm") or {}).get("cost", 0.0)
            semantic, stat = verify(rec, out["judgments"], base)
            judged_false = [
                str(j.get("keyword") or "").strip()
                for j in out["judgments"]
                if str(j.get("verdict")) == "false_gap"
            ]
        elif engine == "cache":
            # 离线重算：只读 runs/cache 里的既有 LLM 输出，**不再调用 API**。
            cache_file = cache_dir / f"{rec['record_id']}.json"
            if not cache_file.exists():
                raise SystemExit(
                    f"离线模式缺少缓存：{cache_file}\n"
                    f"若确实需要重新调用 LLM，请显式加 --engine single（会消耗额度）。"
                )
            out = json.loads(cache_file.read_text(encoding="utf-8"))
            cache_hits += 1
            total_cost += (out.get("llm") or {}).get("cost", 0.0)
            semantic, stat = verify(rec, out["judgments"], base)
            judged_false = [
                str(j.get("keyword") or "").strip()
                for j in out["judgments"]
                if str(j.get("verdict")) == "false_gap"
            ]
        exp_false = set(rec["expected_false_gaps"])
        exp_real = set(rec["expected_real_gaps"])
        got = {kw for kw, _ in semantic}
        rows.append(
            {
                "record_id": rec["record_id"],
                "kind": rec["kind"],
                "missing": len(base["missing"]),
                "expected_false": sorted(exp_false),
                "got_false": sorted(got),
                "judged_false": sorted(set(judged_false)),
                "hit": sorted(exp_false & got),
                "miss": sorted(exp_false - got),
                "wrong": sorted(got & exp_real),
                "located_fail": stat.get("located_fail", 0),
                "v1_ats": scores_for(base, rec, [], tiered=False)["ats"],
                "v2_tier_ats": scores_for(base, rec, [], tiered=True)["ats"],
                "v2_full_ats": scores_for(base, rec, semantic, tiered=True)["ats"],
            }
        )

    elapsed = time.perf_counter() - t0

    # ---- 汇总 ----
    tp = sum(len(r["hit"]) for r in rows)
    fn = sum(len(r["miss"]) for r in rows)
    fp = sum(len(r["wrong"]) for r in rows)
    judged = sum(len(r["judged_false"]) for r in rows)
    loc_fail = sum(r["located_fail"] for r in rows)
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / judged if judged else 0.0
    locate_rate = (judged - loc_fail) / judged if judged else 0.0
    false_positive_rate = fp / (fp + tp) if (fp + tp) else 0.0
    # 证据真实性：被判 false_gap 的条目里，引文确实能在原文定位的比例。
    # 这是 precision 的**上界**——因为夹具的 expected_false_gaps 未穷举，
    # 模型识别出的等价若证据真实，很可能也成立（只是没被声明）。
    evidence_authenticity = locate_rate

    print("=" * 96)
    print(f"engine={engine}  fixtures={len(rows)}  elapsed={elapsed:.1f}s  "
          f"LLM cost=CNY {total_cost:.4f}  cache_hits={cache_hits}")
    print("=" * 96)
    print(f"{'record':12s} {'kind':6s} {'missing':>7s} {'exp':>3s} {'hit':>3s} {'miss':>4s} {'wrong':>5s} "
          f"{'v1_ats':>6s} {'v2tier':>6s} {'v2full':>6s}")
    for r in rows:
        print(f"{r['record_id']:12s} {r['kind']:6s} {r['missing']:>7d} {len(r['expected_false']):>3d} "
              f"{len(r['hit']):>3d} {len(r['miss']):>4d} {len(r['wrong']):>5d} "
              f"{r['v1_ats']:>6d} {r['v2_tier_ats']:>6d} {r['v2_full_ats']:>6d}")
    print("-" * 96)
    print(f"假差距 recall（声明子集） = {tp}/{tp + fn} = {recall:.1%}")
    print(f"假差距 precision（严格）  = {tp}/{judged} = {precision:.1%}   "
          f"← 受限于夹具未穷举 expected_false_gaps，是**下界**")
    print(f"证据定位通过率            = {judged - loc_fail}/{judged} = {locate_rate:.1%}")
    print(f"真差距误判率（全量判定）  = {fp}/{fp + tp} = {false_positive_rate:.1%}   ← 这个才是可信的")
    print(f"ATS 均值：v1 等权={sum(r['v1_ats'] for r in rows) / len(rows):.1f}  "
          f"v2 分层={sum(r['v2_tier_ats'] for r in rows) / len(rows):.1f}  "
          f"v2 分层+语义={sum(r['v2_full_ats'] for r in rows) / len(rows):.1f}")
    print()

    out_dir = EVAL_DIR / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"eval_{engine}_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "engine": engine,
                "fixtures": len(rows),
                "elapsed_s": round(elapsed, 1),
                "cost_cny": round(total_cost, 4),
                "recall": round(recall, 4),
                "precision": round(precision, 4),
                "locate_pass_rate": round(locate_rate, 4),
                "real_gap_false_positive_rate": round(false_positive_rate, 4),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[saved] {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["rule", "single", "cache", "agentic"], default="cache",
                        help="rule=只跑规则层；single=调 LLM；cache=离线复用缓存结果（默认，不调 API）；agentic=LangChain 新链路")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="只做夹具自洽性检查，不调 LLM")
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.engine, args.dry_run))


if __name__ == "__main__":
    main()
