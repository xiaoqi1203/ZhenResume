"""P-1 标注集构建（读 `eval/source_*.jsonl`，不碰 DB、不改生产代码）。

产出两份文件：
1. `backend/labels/prelabel.tsv` —— **人工审阅用**。每行一个"字面缺失关键词"，
   预填了 tier（core/bonus/keyword）、是否在原文出现、出现位置，人工只需填
   `verdict`（real_gap / false_gap）与 `evidence_quote`；
2. `backend/eval/agentic_labels.jsonl` —— **评估脚本读取用**的骨架记录。

设计要点：
- 关键词定位用 `str.find` 在**简历原文**上做，与 `analyze.missing_keywords`
  （在 `resume_haystack` 拼接串上匹配）口径不同 —— 前者回答"原文里有没有"，后者才是规则层结论。
  两者不一致的条目（`literal_in_resume=no` 但规则层判覆盖 / 反之）本身就是要盯的疑点。
- 每个关键词只标注一次（同一 JD 下跨简历共享），但不做跨简历去重 ——
  同一个词在不同简历上是否"真差距"本来就不同，必须逐份判定。

用法（在 backend 目录下）：
    python -m scripts.build_labels --jd-index 0 --limit 30
    python -m scripts.build_labels --jd-index 0 --limit 30 --no-jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import ParsedJd, ParsedResume  # noqa: E402
from app.services import analyze  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
LABELS_DIR = BACKEND_DIR / "labels"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"缺少素材文件：{path}\n请先运行：python -m scripts.dump_samples")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def safe_resume(rec: dict) -> ParsedResume:
    """优先用库里已解析的 parsed_json，失败则回退规则解析。"""
    try:
        return ParsedResume.model_validate(rec.get("parsed_json") or {})
    except Exception:  # noqa: BLE001
        return parse_resume_text(rec.get("raw_text") or "")


def safe_jd(rec: dict) -> ParsedJd:
    try:
        return ParsedJd.model_validate(rec.get("parsed_json") or {})
    except Exception:  # noqa: BLE001
        return parse_jd_text(rec.get("raw_text") or "")


def locate_in_raw(raw_text: str, needle: str) -> tuple[int, int] | None:
    """在原文中定位子串，返回 (行号, 列号)；找不到返回 None。"""
    idx = (raw_text or "").find(needle)
    if idx < 0:
        return None
    line_no = raw_text[:idx].count("\n")
    col = idx - (raw_text.rfind("\n", 0, idx) + 1)
    return line_no, col


def jd_tier(pj: ParsedJd, kw: str) -> str:
    k = (kw or "").lower()
    if any(k == s.lower() for s in pj.skills_core):
        return "core"
    if any(k == s.lower() for s in pj.skills_bonus):
        return "bonus"
    return "keyword"


def build_record(resume: dict, jd: dict, index: int) -> dict:
    raw = resume.get("raw_text") or ""
    jd_raw = jd.get("raw_text") or ""
    pr = safe_resume(resume)
    pj = safe_jd(jd)

    missing, covered = analyze.missing_keywords(pr, pj, raw)
    total = len(missing) + len(covered)
    baseline = analyze.rule_baseline_findings(pr, pj, "general", raw, jd_raw)
    scores_v1 = analyze.compute_scores(baseline, pr, pj, raw)

    return {
        "record_id": f"r{index:02d}_{str(resume.get('id') or '')[:8]}",
        "resume_id": resume.get("id"),
        "resume_title": resume.get("title"),
        "jd_id": jd.get("id"),
        "jd_title": jd.get("title"),
        "raw_text": raw,
        "jd_raw": jd_raw,
        "jd_core": pj.skills_core,
        "jd_bonus": pj.skills_bonus,
        "jd_keywords": pj.keywords,
        "covered": covered,
        "missing": missing,
        "coverage_v1": round(len(covered) / total, 4) if total else None,
        "scores_v1": scores_v1,
        # ---- 人工需要填的部分 ----
        "missing_verdicts": [
            {
                "keyword": kw,
                "tier": jd_tier(pj, kw),
                "literal_in_resume": locate_in_raw(raw, kw) is not None,
                "at": (lambda h: f"L{h[0] + 1}:C{h[1]}" if h else "-")(locate_in_raw(raw, kw)),
                "verdict": "",          # ← 人工：real_gap | false_gap
                "evidence_quote": "",   # ← verdict=false_gap 时必填，须为原文连续片段
                "note": "",
            }
            for kw in missing
        ],
        "human_overall_band": "",       # ← 人工：strong | medium | weak（分档比精确排名更可靠）
        "annotator": "",
        "annotated_at": "",
    }


def write_prelabel_tsv(records: list[dict]) -> Path:
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    out = LABELS_DIR / "prelabel.tsv"
    header = [
        "record_id", "jd_title", "keyword", "tier", "literal_in_resume", "at",
        "verdict", "confidence", "evidence_quote", "note",
    ]
    lines = ["\t".join(header)]
    for rec in records:
        for item in rec["missing_verdicts"]:
            lines.append(
                "\t".join(
                    [
                        rec["record_id"],
                        str(rec["jd_title"] or ""),
                        item["keyword"],
                        item["tier"],
                        "yes" if item["literal_in_resume"] else "no",
                        item["at"],
                        item["verdict"],
                        "",
                        item["evidence_quote"],
                        item["note"],
                    ]
                )
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jd-index", type=int, default=0, help="用第几个 JD（默认 0）")
    parser.add_argument("--limit", type=int, default=30, help="取多少份简历（默认 30）")
    parser.add_argument("--no-jsonl", action="store_true", help="不写 JSONL")
    args = parser.parse_args()

    resumes = load_jsonl(EVAL_DIR / "source_resumes.jsonl")
    jds = load_jsonl(EVAL_DIR / "source_jds.jsonl")
    if not jds:
        raise SystemExit("source_jds.jsonl 为空")
    jd = jds[max(0, min(args.jd_index, len(jds) - 1))]
    picked = resumes[: args.limit]

    records = [build_record(r, jd, i) for i, r in enumerate(picked)]

    tsv = write_prelabel_tsv(records)
    rows = sum(len(r["missing_verdicts"]) for r in records)
    print(f"[prelabel] {tsv}   {rows} 行待人工判定（{len(records)} 份简历 × JD「{jd.get('title')}」）")

    if not args.no_jsonl:
        EVAL_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_DIR / "agentic_labels.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[jsonl]    {path}   {len(records)} 条记录")

    print()
    print(f"JD：{jd.get('title')}  core={len(records[0]['jd_core'])}  "
          f"bonus={len(records[0]['jd_bonus'])}  keywords={len(records[0]['jd_keywords'])}")
    print(f"{'record_id':22s} {'覆盖':>7s} {'缺失':>5s} {'coverage_v1':>12s} {'overall':>8s} {'ats':>5s} {'match':>6s}")
    for rec in records:
        total = len(rec["missing"]) + len(rec["covered"])
        sc = rec["scores_v1"]
        print(
            f"{rec['record_id']:22s} {len(rec['covered']):>3d}/{total:<3d} "
            f"{len(rec['missing']):>5d} {rec['coverage_v1'] or 0:>12.2%} "
            f"{sc['overall']:>8d} {sc['ats']:>5d} {sc['match']:>6d}"
        )


if __name__ == "__main__":
    main()
