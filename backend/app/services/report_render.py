"""报告渲染：Markdown 导出（契约 §5.2）。

不做 PDF：MVP 阶段 Markdown 能被浏览器直接打印成 PDF，且零依赖；
后续接 Playwright / WeasyPrint 时复用本模块的渲染结果即可。
"""

from __future__ import annotations

SEVERITY_LABEL = {"red": "🔴 高风险", "yellow": "🟡 需优化", "green": "🟢 表现良好"}
CATEGORY_LABEL = {
    "ats": "ATS 匹配",
    "impact": "影响力",
    "clarity": "清晰度",
    "risk": "风险",
    "gap": "竞争力差距",
}
SOURCE_LABEL = {"resume": "简历原文", "jd": "JD 原文", "knowledge": "岗位知识库"}


def render_markdown(report: dict) -> str:
    scores = report.get("scores") or {}
    findings = report.get("findings") or []
    lines: list[str] = []

    lines.append(f"# 简甄 ZhenResume 诊断报告")
    lines.append("")
    lines.append(f"- 简历：{report.get('resume_title') or '未命名'}")
    lines.append(f"- 目标岗位：{report.get('jd_title') or '未指定'}")
    lines.append(f"- 生成时间：{report.get('created_at') or ''}")
    lines.append(f"- 报告 ID：`{report.get('report_id')}`")
    lines.append("")

    lines.append("## 一、总分与维度分")
    lines.append("")
    lines.append("| 维度 | 得分 | 权重 |")
    lines.append("|---|---|---|")
    lines.append(f"| **综合得分** | **{scores.get('overall', 0)}** | — |")
    lines.append(f"| ATS 匹配 | {scores.get('ats', 0)} | 30% |")
    lines.append(f"| 影响力 | {scores.get('impact', 0)} | 25% |")
    lines.append(f"| 清晰度 | {scores.get('clarity', 0)} | 20% |")
    lines.append(f"| 竞争力差距 | {scores.get('match', 0)} | 15% |")
    lines.append(f"| 风险安全度 | {scores.get('risk', 0)} | 10% |")
    lines.append("")
    if report.get("summary"):
        lines.append(f"> {report['summary']}")
        lines.append("")

    counts = {sev: len([f for f in findings if f.get("severity") == sev]) for sev in ("red", "yellow", "green")}
    lines.append("## 二、风险概览")
    lines.append("")
    lines.append(f"🔴 高风险 {counts['red']} 条 ｜ 🟡 需优化 {counts['yellow']} 条 ｜ 🟢 表现良好 {counts['green']} 条")
    lines.append("")

    lines.append("## 三、逐条发现")
    lines.append("")
    if not findings:
        lines.append("_未发现明显问题。_")
        lines.append("")
    for index, finding in enumerate(findings, 1):
        sev = finding.get("severity", "yellow")
        lines.append(f"### {index}. {SEVERITY_LABEL.get(sev, sev)} ｜ {finding.get('title', '')}")
        lines.append("")
        lines.append(f"- **类别**：{CATEGORY_LABEL.get(finding.get('category', ''), finding.get('category', ''))}")
        if finding.get("original_text"):
            lines.append(f"- **原文**：`{finding['original_text']}`")
        lines.append(f"- **问题**：{finding.get('issue', '')}")
        evidence = finding.get("evidence") or {}
        if evidence.get("quote"):
            source = SOURCE_LABEL.get(evidence.get("source", "resume"), "原文")
            span = evidence.get("span")
            span_text = f"（字符位置 {span[0]}–{span[1]}）" if isinstance(span, list) and len(span) == 2 else "（未能在原文中定位）"
            lines.append(f"- **证据**：{source}「{evidence['quote']}」{span_text}")
        else:
            # 覆盖率、篇幅这类结论基于全文统计，本来就没有单条引文，如实说明而不是留空
            lines.append("- **证据**：该结论基于全文统计，无单条引文")
        lines.append(f"- **建议**：{finding.get('suggestion', '')}")
        if finding.get("rewritten_text"):
            lines.append("")
            lines.append("**改写句（可直接复制）：**")
            lines.append("")
            lines.append("```")
            lines.append(str(finding["rewritten_text"]))
            lines.append("```")
        lines.append("")

    missing = report.get("missing_keywords") or []
    lines.append("## 四、缺失关键词与建议补充")
    lines.append("")
    lines.append("、".join(f"`{kw}`" for kw in missing) if missing else "_无缺失关键词。_")
    lines.append("")

    actions = report.get("action_items") or []
    lines.append("## 五、下一步行动清单")
    lines.append("")
    if actions:
        for i, action in enumerate(actions, 1):
            lines.append(f"{i}. {action}")
    else:
        lines.append("_暂无行动项。_")
    lines.append("")

    refs = report.get("knowledge_refs") or []
    if refs:
        lines.append("## 六、知识库依据")
        lines.append("")
        for ref in refs:
            lines.append(f"- [{ref.get('scope', '')}] {ref.get('title', '')}（相关度 {ref.get('score', '')}）")
        lines.append("")

    lines.append("## 免责声明")
    lines.append("")
    lines.append(report.get("disclaimer") or "本报告由 AI 生成，仅供参考。")
    lines.append("")
    return "\n".join(lines)
