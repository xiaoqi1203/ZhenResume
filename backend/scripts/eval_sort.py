"""P2-b/P2-c：排序质量评估（分组 ATS 单调性 + Spearman）。

对 `eval/sort_fixtures/` 的 32 份夹具，按四种口径算分并检验排序：

| 口径 | 说明 |
|---|---|
| `v1` | 现状：等权子串覆盖率、无语义 |
| `v2-tier` | 分层加权（core 1.0 / bonus 0.6 / keyword 0.3）、无语义 |
| `v2-full` | 分层加权 + 语义校验（需 `--engine cache/single`） |
| `oracle` | **上界**：把 ground truth 的等价项直接当作已覆盖，不经过模型 |

`oracle` 的作用是给出"语义判断 100% 正确"时的理论上限；`v2-full` 与它的差距
就是"模型判断质量"造成的损失，可直接归因。

排序期望：组 A（设计覆盖最高）> B > C > D。检验指标：
1. **组间 ATS 是否单调**（A>B>C>D）——最直观；
2. **Spearman(设计覆盖项数, ATS)**——连续口径；
3. **假差距被救回的幅度**：`v2-full − v2-tier`，按组看是否 A 组最大（A 组等价项最多）。

用法（在 backend 目录下）：
    python -m scripts.eval_sort --engine rule          # 只算 v1/v2-tier/oracle，不调 LLM
    python -m scripts.eval_sort --engine cache         # 复用 LLM 判定缓存
    python -m scripts.eval_sort --engine single        # 调 LLM（写入缓存）
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # 用 reconfigure 而不是 io.TextIOWrapper：后者会接管 buffer 所有权，
    # 包装对象被回收时会把 stdout 关掉（实测报 "I/O operation on closed file"）。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services import analyze  # noqa: E402
from app.services.evidence import locate_quote  # noqa: E402
from app.services.llm import get_llm  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from app.services.semantic import verify_semantic_judgments  # noqa: E402
from scripts.eval_agentic import SYSTEM_PROMPT, USER_TEMPLATE  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
FIX_DIR = EVAL_DIR / "sort_fixtures"
CACHE_DIR = EVAL_DIR / "runs" / "cache_sort"


def load() -> list[dict]:
    recs = [json.loads(l) for l in (EVAL_DIR / "sort_expected.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    jd_raw = (FIX_DIR / "jd_backend.txt").read_text(encoding="utf-8")
    for r in recs:
        r["raw_text"] = (FIX_DIR / r["file"]).read_text(encoding="utf-8")
        r["jd_raw"] = jd_raw
    return recs


def score_all(rec: dict, semantic: list[tuple[str, str]]) -> dict:
    """一次算出三种口径的 ATS（都调用生产实现，保证与线上一致）。"""
    pr = parse_resume_text(rec["raw_text"])
    pj = parse_jd_text(rec["jd_raw"])
    out: dict[str, int] = {}
    object.__setattr__(settings, "scoring_version", "v1")
    out["v1"] = analyze.compute_scores_deterministic(pr, pj, "backend", rec["raw_text"], rec["jd_raw"])["ats"]
    object.__setattr__(settings, "scoring_version", "v2")
    out["v2_tier"] = analyze.compute_scores_deterministic(pr, pj, "backend", rec["raw_text"], rec["jd_raw"])["ats"]
    s_full = analyze.compute_scores_deterministic(
        pr, pj, "backend", rec["raw_text"], rec["jd_raw"], semantic_covered=semantic or None
    )
    out["v2_full"] = s_full["ats"]
    return out


async def judge(rec: dict, llm, *, engine: str, min_evidence: bool = False) -> list[tuple[str, str]]:
    """得到本份的语义等价列表（经硬校验）。engine 决定数据来源。"""
    pr = parse_resume_text(rec["raw_text"])
    pj = parse_jd_text(rec["jd_raw"])
    missing, covered = analyze.missing_keywords(pr, pj, rec["raw_text"])
    if not missing:
        return []

    if engine == "oracle":
        # 上界：直接把 ground truth 的等价项当作已覆盖，并为其在原文中找一段真实引文
        verified: list[tuple[str, str]] = []
        for kw in rec["expected_false_gaps"]:
            quote = _find_quote_for(rec, kw)
            if quote and locate_quote(rec["raw_text"], quote):
                verified.append((kw, quote))
        return verified

    if engine == "rule":
        return []  # 无语义：只看 v1 / v2-tier 基线

    if engine == "langchain":
        # P2-c：LangChain Agent（多轮工具 + 自主收敛 + locate_quote 自检）
        import hashlib
        from app.services.agent_langchain import run_semantic_agent_langchain

        key = hashlib.sha256(
            (rec["record_id"] + rec["raw_text"] + rec["jd_raw"]).encode("utf-8")
        ).hexdigest()[:16]
        cache_file = CACHE_DIR / f"{rec['record_id']}.lc.{key}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return [(k, q) for k, q in data.get("semantic_covered", [])]
        run = await run_semantic_agent_langchain(
            resume_text=rec["raw_text"], jd_text=rec["jd_raw"],
            parsed_resume=pr, parsed_jd=pj, role_family="backend",
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "record_id": rec["record_id"], "engine": "langchain",
                    "semantic_covered": run.semantic_covered,
                    "judgments": run.judgments, "trace": run.trace,
                    "verify_stats": run.verify_stats, "notes": run.notes,
                    "status": run.status,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return run.semantic_covered

    if engine == "semantic":
        # P2-a 对照臂：单轮工具 + 一次结构化产出（不走 Agent 循环）
        # 缓存键含夹具内容哈希，夹具变了自动失效，避免复用过期判定。
        import hashlib
        from app.services.semantic_agent import run_semantic_single_pass

        key = hashlib.sha256(
            (rec["record_id"] + rec["raw_text"] + rec["jd_raw"]).encode("utf-8")
        ).hexdigest()[:16]
        cache_file = CACHE_DIR / f"{rec['record_id']}.{key}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return [(k, q) for k, q in data.get("semantic_covered", [])]
        run = await run_semantic_single_pass(
            resume_text=rec["raw_text"], jd_text=rec["jd_raw"], llm=llm,
            parsed_resume=pr, parsed_jd=pj, role_family="backend",
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "record_id": rec["record_id"], "engine": "semantic",
                    "semantic_covered": run.semantic_covered,
                    "judgments": run.judgments, "trace": run.trace,
                    "verify_stats": run.verify_stats, "notes": run.notes,
                    "status": run.status,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return run.semantic_covered

    cache_file = CACHE_DIR / f"{rec['record_id']}.json"
    if engine == "cache" or (engine == "single" and cache_file.exists()):
        if not cache_file.exists():
            raise SystemExit(f"离线模式缺少缓存：{cache_file}（请先跑 --engine single）")
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        judgments = data.get("judgments") or []
    else:
        user = USER_TEMPLATE.format(missing="、".join(missing), resume=rec["raw_text"])
        res = await llm.complete_json(
            system=SYSTEM_PROMPT, user=user, mock_factory=lambda: {"judgments": []}, agent="sort_eval"
        )
        judgments = res.data.get("judgments") or []
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"record_id": rec["record_id"], "missing": missing, "judgments": judgments,
                        "llm": {"model": res.model, "cost": res.cost, "latency_ms": res.latency_ms}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return _verify(rec, judgments, missing, min_evidence=min_evidence)


def _verify(rec: dict, judgments: list[dict], missing: list[str], *, min_evidence: bool = False) -> list[tuple[str, str]]:
    """与生产同口径的校验（见改动说明书 §6.2），可选叠加"证据强度"过滤。

    两种模式都调用**生产实现** `services/semantic.verify_semantic_judgments`，
    因此评估与线上不会漂移：
    - `min_evidence=False`：只要求引文可定位（用于对比"仅可定位"的效果）；
    - `min_evidence=True`：叠加证据强度（生产默认）。

    为什么要区分：`locate_quote` 只能证明"引文不是编的"，
    **无法证明"这条引文足以支撑这个结论"**。实测这两件事的差距是
    Spearman +0.835（无语义）→ +0.756（仅可定位）→ **+0.847（叠加强度）**。
    """
    pr = parse_resume_text(rec["raw_text"])
    pj = parse_jd_text(rec["jd_raw"])
    _missing2, covered = analyze.missing_keywords(pr, pj, rec["raw_text"])
    passed, _stats = verify_semantic_judgments(
        resume_text=rec["raw_text"],
        parsed_jd=pj,
        missing_keywords=missing,
        covered_keywords=covered,
        judgments=judgments,
        require_strength=min_evidence,
    )
    return passed


def _find_quote_for(rec: dict, kw: str) -> str:
    """oracle 模式：在本份简历里为等价项找一段真实存在的引文。

    优先用该维度在语料里登记的等价措辞（取其中一句），否则退化为按关键词所在行取片段。
    """
    from scripts.sort_fixture_data import DIMENSIONS

    text = rec["raw_text"]
    equiv = DIMENSIONS.get(kw, {}).get("equiv", "")
    for part in [p.strip() for p in equiv.split("；") if p.strip()]:
        if locate_quote(text, part):
            return part
    return ""


def spearman(xs: list[float], ys: list[float]) -> float:
    def rank(vs: list[float]) -> list[float]:
        order = sorted(range(len(vs)), key=lambda i: vs[i])
        r = [0.0] * len(vs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


async def run(engine: str, min_evidence: bool = False) -> None:
    recs = load()
    llm = get_llm() if engine in ("single", "semantic") else None
    t0 = time.perf_counter()
    cost = 0.0

    failures: list[str] = []
    for rec in recs:
        try:
            sem = await judge(rec, llm, engine=engine, min_evidence=min_evidence)
        except Exception as exc:  # noqa: BLE001 —— 单份失败（额度/网络）不应中断整轮
            failures.append(f"{rec['record_id']}: {type(exc).__name__}: {str(exc)[:80]}")
            sem = []
        rec["semantic"] = [kw for kw, _ in sem]
        rec["scores"] = score_all(rec, sem)
    if failures:
        print(f"\n⚠️ {len(failures)} 份判定失败（已按无判定计入）：")
        for f in failures:
            print("   ", f)

    elapsed = time.perf_counter() - t0

    # ---- 逐份明细 ----
    print("=" * 104)
    print(f"engine={engine}{' +min-evidence' if min_evidence else ''}  fixtures={len(recs)}  elapsed={elapsed:.1f}s")
    print("=" * 104)
    print(f"{'record':12s} {'grp':4s} {'设计覆盖':>8s} {'期望假差距':>10s} {'判出':>5s} {'期望真差距':>10s} "
          f"{'误判':>5s} {'v1':>4s} {'v2tier':>7s} {'v2full':>7s}")
    for rec in recs:
        got = set(rec["semantic"])
        exp_false = set(rec["expected_false_gaps"])
        exp_real = set(rec["expected_real_gaps"])
        print(f"{rec['record_id']:12s} {rec['group']:4s} {rec['design_coverage']:>8d} "
              f"{len(exp_false):>10d} {len(exp_false & got):>5d} {len(exp_real):>10d} "
              f"{len(got & exp_real):>5d} {rec['scores']['v1']:>4d} {rec['scores']['v2_tier']:>7d} "
              f"{rec['scores']['v2_full']:>7d}")

    # ---- 分组统计 ----
    print("-" * 104)
    print(f"{'group':6s} {'n':>3s} {'设计覆盖均值':>12s} {'ATS v1':>8s} {'ATS v2tier':>11s} {'ATS v2full':>11s} {'救回幅度':>9s}")
    group_stats: dict[str, dict] = {}
    for g in ("A", "B", "C", "D"):
        rows = [r for r in recs if r["group"] == g]
        if not rows:
            continue
        dc = sum(r["design_coverage"] for r in rows) / len(rows)
        v1 = sum(r["scores"]["v1"] for r in rows) / len(rows)
        v2t = sum(r["scores"]["v2_tier"] for r in rows) / len(rows)
        v2f = sum(r["scores"]["v2_full"] for r in rows) / len(rows)
        group_stats[g] = {"n": len(rows), "design_coverage": dc, "v1": v1, "v2tier": v2t, "v2full": v2f, "gain": v2f - v2t}
        print(f"{g:6s} {len(rows):>3d} {dc:>12.2f} {v1:>8.1f} {v2t:>11.1f} {v2f:>11.1f} {v2f - v2t:>9.1f}")

    print("-" * 104)
    monotone = all(
        group_stats[a]["v2tier"] >= group_stats[b]["v2tier"]
        for a, b in zip("ABCD", "BCD")
        if a in group_stats and b in group_stats
    )
    print(f"组间单调性（A≥B≥C≥D，按 v2-tier）：{'✅ 成立' if monotone else '❌ 不成立'}")

    dc_all = [r["design_coverage"] for r in recs]
    for label, key in (("v1", "v1"), ("v2-tier", "v2_tier"), ("v2-full", "v2_full")):
        rho = spearman([float(x) for x in dc_all], [float(r["scores"][key]) for r in recs])
        print(f"Spearman(设计覆盖项数, ATS {label:8s}) = {rho:+.3f}")

    # 假差距救回幅度按组
    print()
    print("假差距识别（recall）与误判（precision 的反面）：")
    tp = sum(len(set(r["expected_false_gaps"]) & set(r["semantic"])) for r in recs)
    fn = sum(len(set(r["expected_false_gaps"]) - set(r["semantic"])) for r in recs)
    fp = sum(len(set(r["semantic"]) & set(r["expected_real_gaps"])) for r in recs)
    print(f"  recall    = {tp}/{tp + fn} = {tp / (tp + fn):.1%}" if (tp + fn) else "  recall    = n/a")
    print(f"  误判真差距 = {fp} 条（期望真差距被判成等价）")

    out_dir = EVAL_DIR / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ("_minedge" if min_evidence else "") + ("_sp" if engine == "semantic" else "") + ("_lc" if engine == "langchain" else "")
    path = out_dir / f"eval_sort_{engine}{suffix}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(
        json.dumps({"engine": engine, "groups": group_stats, "records": recs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[saved] {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["rule", "oracle", "cache", "single", "semantic", "langchain"], default="rule")
    parser.add_argument("--min-evidence", action="store_true", help="叠加证据强度过滤（排除技能栏裸词/过短引文）")
    args = parser.parse_args()
    asyncio.run(run(args.engine, min_evidence=args.min_evidence))


if __name__ == "__main__":
    main()
