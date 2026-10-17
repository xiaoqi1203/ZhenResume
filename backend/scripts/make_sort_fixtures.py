"""P2-b：生成**排序验证**夹具，并与规则层对齐 ground truth。

产出：
    backend/eval/sort_fixtures/sort_XX.txt      32 份简历
    backend/eval/sort_fixtures/jd_backend.txt   统一 JD（取自真实库导出）
    backend/eval/sort_expected.jsonl            逐维度标签（完整，无遗漏）

对齐机制（沿用 P-1 的教训）：ground truth 必须与规则层口径一致。
`real_gap` 只在**规则层确实判缺失**时才计入；若某个设计为 real_gap 的维度
被规则层判为覆盖（说明夹具写漏了），会被记录到 `unregistered_real_gaps` 供排查。

用法（在 backend 目录下）：
    python -m scripts.make_sort_fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import analyze  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from scripts.sort_fixture_data import (  # noqa: E402
    BASES,
    DIMENSIONS,
    GROUPS,
    PER_GROUP,
    build_resume,
    plan,
)

EVAL_DIR = BACKEND_DIR / "eval"
FIX_DIR = EVAL_DIR / "sort_fixtures"
SOURCE_JD = EVAL_DIR / "source_jds.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=PER_GROUP, help="每组份数")
    args = parser.parse_args()

    FIX_DIR.mkdir(parents=True, exist_ok=True)
    if SOURCE_JD.exists():
        jd = json.loads(SOURCE_JD.read_text(encoding="utf-8").splitlines()[0])
        (FIX_DIR / "jd_backend.txt").write_text(jd["raw_text"], encoding="utf-8")
    else:
        raise SystemExit("缺少 eval/source_jds.jsonl，请先运行 python -m scripts.dump_samples")

    jd_raw = (FIX_DIR / "jd_backend.txt").read_text(encoding="utf-8")
    pj = parse_jd_text(jd_raw)

    items = plan()
    # 按 --limit 截断每组
    kept: list[dict] = []
    per_group: dict[str, int] = {}
    for item in items:
        g = item["group"]
        per_group.setdefault(g, 0)
        if per_group[g] >= args.limit:
            continue
        per_group[g] += 1
        kept.append(item)

    records: list[dict] = []
    for i, item in enumerate(kept):
        base = BASES[item["base_index"]]
        text = build_resume(
            base, literal=item["literal"], equiv=item["equiv"],
            real_gap=item["real_gap"], index=i,
        )
        (FIX_DIR / f"{item['record_id']}.txt").write_text(text, encoding="utf-8")

        pr = parse_resume_text(text)
        missing, covered = analyze.missing_keywords(pr, pj, text)
        missing_lower = {m.lower() for m in missing}
        covered_lower = {c.lower() for c in covered}

        designed_real = list(item["real_gap"])
        effective_real = [k for k in designed_real if k.lower() in missing_lower]
        unregistered_real = [k for k in designed_real if k.lower() not in missing_lower]
        effective_equiv = [k for k in item["equiv"] if k.lower() in missing_lower]

        records.append(
            {
                "record_id": item["record_id"],
                "group": item["group"],
                "file": f"{item['record_id']}.txt",
                "designed_literal": item["literal"],
                "designed_equiv": item["equiv"],
                "designed_real_gaps": designed_real,
                # ground truth（已与规则层对齐）
                "expected_false_gaps": effective_equiv,     # 语义层应识别为表述差异
                "expected_real_gaps": effective_real,       # 语义层应判为真差距
                "unregistered_real_gaps": unregistered_real,
                "expected_covered": [k for k in covered if k in DIMENSIONS],
                "rule_missing_all": missing,
                "rule_covered_all": covered,
            }
        )

    # 组内按"设计覆盖项数"降序，作为排序的期望顺序（质量代理）
    for rec in records:
        rec["design_coverage"] = len(rec["designed_literal"]) + len(rec["designed_equiv"])

    with (EVAL_DIR / "sort_expected.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[OK] {len(records)} 份排序夹具 → {FIX_DIR}")
    print(f"[OK] ground truth      → {EVAL_DIR / 'sort_expected.jsonl'}")
    print()
    print(f"JD 要求 {len(pj.all_skills())} 项（core={len(pj.skills_core)} bonus={len(pj.skills_bonus)}）")
    print(f"{'record':12s} {'group':5s} {'设计覆盖':>8s} {'期望假差距':>10s} {'期望真差距':>10s} {'规则缺失':>8s}")
    for rec in records:
        print(f"{rec['record_id']:12s} {rec['group']:5s} {rec['design_coverage']:>8d} "
              f"{len(rec['expected_false_gaps']):>10d} {len(rec['expected_real_gaps']):>10d} "
              f"{len(rec['rule_missing_all']):>8d}")

    unreg = [r for r in records if r["unregistered_real_gaps"]]
    print()
    if unreg:
        print("⚠️ 以下夹具的「设计真差距」未被规则层判缺失（ground truth 已剔除，请检查夹具写法）：")
        for r in unreg:
            print(f"   {r['record_id']}: {r['unregistered_real_gaps']}")
    else:
        print("✅ ground truth 与规则层完全一致，无未注册项。")


if __name__ == "__main__":
    main()
