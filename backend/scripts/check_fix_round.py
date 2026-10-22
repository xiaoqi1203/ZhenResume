"""针对本轮修复的后端自检（在 api 容器内执行）。

覆盖：
1. JD 「Java 或 Go」→ skill_groups 识别
2. 简历只有 Java 时，"Go" 不应算缺失（择一命中）
3. 两项都不具备时才报缺失，且合并为一条「Java 或 Go」
4. 风险规则不再把年龄/性别当作问题项
5. 任意 finding 里拿年龄/性别说事都会被 sort_findings 过滤
"""

from app.schemas import ParsedJd, ParsedResume
from app.services import analyze
from app.services.parse_rules import parse_jd_text

JD = """
岗位名称：后端开发工程师
岗位职责：
1. 负责核心交易系统的服务端开发；
2. 参与架构设计与性能优化。
任职要求：
1. 熟悉 Java 或 Go，有扎实的编程基础；
2. 熟悉 MySQL、Redis；
3. 本科及以上学历。
"""

RESUME_WITH_JAVA = """
张三
1996 年出生，男
专业技能：Java、Spring Boot、MySQL、Redis、Kafka
工作经历
某科技有限公司 | 后端开发工程师 | 2020.07 - 2024.06
- 负责交易系统 Java 服务开发，QPS 从 800 提升到 3000
"""

RESUME_WITHOUT_BOTH = """
李四
专业技能：Python、Django、PostgreSQL
工作经历
某公司 | Python 开发 | 2021.03 - 2024.01
- 用 Django 开发内部管理系统
"""


def build_resume(text: str) -> ParsedResume:
    from app.services.parse_rules import parse_resume_text

    return parse_resume_text(text)


def main() -> None:
    failures: list[str] = []

    jd = parse_jd_text(JD)
    print("skill_groups =", jd.skill_groups)
    if not any(set(g) >= {"Java", "Go"} for g in jd.skill_groups):
        failures.append("未识别出「Java 或 Go」择一组")

    with_java = build_resume(RESUME_WITH_JAVA)
    missing, covered = analyze.missing_keywords(with_java, jd, RESUME_WITH_JAVA)
    print("只有 Java → missing =", missing)
    print("只有 Java → covered =", [c for c in covered if "或" in c])
    if any("go" in m.lower() for m in missing):
        failures.append("简历有 Java 却仍报缺 Go")
    if not any("或" in c for c in covered):
        failures.append("命中择一时未记为已覆盖")

    without_both = build_resume(RESUME_WITHOUT_BOTH)
    missing2, _ = analyze.missing_keywords(without_both, jd, RESUME_WITHOUT_BOTH)
    print("两项都没有 → missing =", missing2)
    group_missing = [m for m in missing2 if "或" in m]
    if len(group_missing) != 1:
        failures.append(f"择一全缺时应合并为 1 条，实际 {group_missing}")

    # 合规：年龄/性别不再作为问题项
    risky = analyze.risk_findings(build_resume(RESUME_WITH_JAVA), RESUME_WITH_JAVA)
    print("risk_findings titles =", [f["title"] for f in risky])
    if any("年龄" in f["title"] or "性别" in f["title"] or "敏感" in f["title"] for f in risky):
        failures.append("风险规则仍在把年龄/性别当作问题")

    filtered = analyze.sort_findings(
        [
            {"category": "risk", "severity": "yellow", "title": "年龄偏大", "issue": "建议删除年龄"},
            {"category": "ats", "severity": "yellow", "title": "缺少 Redis 关键词", "issue": "补充", "suggestion": ""},
        ]
    )
    print("sort_findings →", [f["title"] for f in filtered])
    if any("年龄" in f["title"] for f in filtered):
        failures.append("sort_findings 未过滤年龄相关条目")

    if failures:
        print("\nFAILED:")
        for item in failures:
            print(" -", item)
        raise SystemExit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
