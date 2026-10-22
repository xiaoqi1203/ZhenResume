"""第二轮修复自检：择一识别、按比例计分、重复建议去重、近义建议移除。"""

from app.services import analyze
from app.services.parse_rules import parse_jd_text, parse_resume_text

JD = """
岗位名称：后端开发工程师
岗位职责：
1. 负责服务端业务开发与性能优化；
任职要求：
1. 熟练掌握 C/C++/Java/Go 等其中一门开发语言，具备良好的编程习惯；
2. 熟悉 MySQL、Redis，有高并发经验优先；
3. 本科及以上学历。
"""

RESUME = """
张三
专业技能：Java、Spring Boot、MySQL、Redis、Kafka
工作经历
某科技 | 后端开发工程师 | 2020.07 - 2024.06
- 负责交易系统 Java 服务开发，QPS 从 800 提升到 3000
- 主导 Redis 缓存改造，P99 从 420ms 降到 90ms
"""


def main() -> None:
    failures: list[str] = []
    jd = parse_jd_text(JD)
    resume = parse_resume_text(RESUME)
    print("skill_groups =", jd.skill_groups)

    group = next((g for g in jd.skill_groups if "Java" in g), None)
    if not group:
        failures.append("未识别「C/C++/Java/Go 等其中一门」")
    elif len(group) < 3:
        failures.append(f"择一组成员过少：{group}")

    # 1) ATS 不应再报缺 Go / C++
    ats = analyze.ats_findings(resume, jd, RESUME, JD)
    ats_titles = [f["title"] for f in ats]
    print("ats titles =", ats_titles)
    if any(("Go" in t or "C++" in t) for t in ats_titles):
        failures.append(f"ATS 仍把择一组内未写的语言当缺失：{ats_titles}")

    # 2) 择一组命中 → 组内成员都算满足（供 LLM 侧过滤）
    satisfied = analyze.satisfied_choice_keywords(resume, jd, RESUME)
    print("satisfied_choice =", sorted(satisfied))
    if not {"java", "go", "c++"} <= satisfied:
        failures.append(f"择一满足集合不完整：{satisfied}")

    # 3) 覆盖率按成员比例：简历会 Java + MySQL + Redis
    units = analyze.coverage_units(resume, jd, RESUME)
    total = sum(len(m) for m, _ in units)
    hits = sum(len(h) for _, h in units)
    print(f"coverage = {hits}/{total} = {hits / total:.0%}")
    lang = next((u for u in units if "Java" in u[0]), None)
    if not lang or lang[1] != ["Java"]:
        failures.append(f"择一组命中应为 ['Java']，实际 {lang}")

    # 4) 不再产出「重合度偏低」建议
    gaps = analyze.gap_findings(resume, jd, RESUME, JD)
    gap_titles = [g["title"] for g in gaps]
    print("gap titles =", gap_titles)
    if any("重合度偏低" in t for t in gap_titles):
        failures.append("仍产出「重合度偏低」建议")

    # 5) 同一关键词重复条目只留一条
    findings = [
        {"category": "ats", "severity": "red", "title": "JD 要求的关键词「Kafka」在简历中缺失", "issue": "", "suggestion": ""},
        {"category": "gap", "severity": "red", "title": "岗位要求「Kafka」在简历中缺少对应证据", "issue": "", "suggestion": ""},
    ]
    kept = [f["title"] for f in analyze.sort_findings(findings)]
    print("dedup ->", kept)
    if len(kept) != 1:
        failures.append(f"同关键词重复条目未去重：{kept}")

    if failures:
        print("\nFAILED:")
        for item in failures:
            print(" -", item)
        raise SystemExit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
