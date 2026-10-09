"""JD 匹配 Agent：关键词覆盖、真实差距与表述差异的区分。

规则层给出「字面缺失」与「年限/学历差距」的硬事实；
LLM 负责区分「真差距」与「表述差异」（例如简历写「服务治理」而 JD 写「微服务」），
因此本 Agent 允许 LLM 从缺失清单里剔除误报 —— 这是唯一允许 LLM 推翻规则结论的地方。
"""

from __future__ import annotations

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .finding_utils import coerce_findings, merge_findings


class JdMatchAgent(Agent):
    name = "jd_match"
    title = "JD 匹配 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        missing, covered = analyze.missing_keywords(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        baseline_gaps = coerce_findings(
            analyze.gap_findings(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text, ctx.jd_text),
            default_category="gap",
            ctx=ctx,
        )

        total = len(missing) + len(covered)
        ratio = (len(covered) / total) if total else 0.0
        baseline_summary = (
            f"目标岗位「{ctx.parsed_jd.title or '未命名岗位'}」共识别出 {total} 项能力要求，"
            f"简历覆盖 {len(covered)} 项（{ratio:.0%}）。"
            + (f"未覆盖：{'、'.join(missing[:6])}。" if missing else "关键词覆盖完整。")
        )

        def mock_factory() -> dict:
            # real_gaps 留空：关键词缺失已由 ATS Agent 逐条产出，规则层再重复一遍
            # 会挤占报告条数（红色名额被关键词问题占满，真正的风险项反而被截断）。
            # 这个字段的用途是「LLM 发现的、规则层没识别出的语义差距」，mock 模式下不存在此能力。
            return {
                "match_summary": baseline_summary,
                "real_gaps": [],
                "false_gaps": [],
                "coverage_comment": f"关键词覆盖率 {ratio:.0%}",
            }

        user = (
            f"【岗位】{ctx.parsed_jd.title}（{ctx.parsed_jd.seniority or '年限未标注'}）\n"
            f"【核心技能】{'、'.join(ctx.parsed_jd.skills_core) or '未识别'}\n"
            f"【加分技能】{'、'.join(ctx.parsed_jd.skills_bonus) or '无'}\n"
            f"【JD 关键词】{'、'.join(ctx.parsed_jd.keywords) or '无'}\n"
            f"【关键词缺失清单】{'、'.join(missing) or '无'}\n"
            f"【已覆盖关键词】{'、'.join(covered[:20]) or '无'}\n\n"
            f"【简历技能栏】{'、'.join(ctx.parsed_resume.skills) or '未识别'}\n"
            f"【简历经历摘要】\n{_resume_digest(ctx)}\n\n"
            f"【岗位知识库依据】\n{ctx.knowledge_digest()}\n\n"
            "请判断缺失清单中哪些是真差距、哪些只是表述差异（能力其实具备但用词不同），并给出匹配度总结。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("jd_match_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        data = result.data or {}
        false_gaps = {
            str(item.get("keyword", "")).strip()
            for item in (data.get("false_gaps") or [])
            if isinstance(item, dict) and str(item.get("keyword", "")).strip()
        }
        llm_gaps = coerce_findings(data.get("real_gaps_as_findings"), default_category="gap", ctx=ctx)

        # 剔除被判定为"表述差异"的误报
        kept_gaps = [f for f in baseline_gaps if not _suppressed_by(f, false_gaps)]
        ctx.findings = [f for f in ctx.findings if not _suppressed_by(f, false_gaps)]

        # 补充 LLM 判定的真实差距（规则层没识别出的语义差距）
        for gap in data.get("real_gaps") or []:
            if not isinstance(gap, dict):
                continue
            keyword = str(gap.get("keyword") or "").strip()
            reason = str(gap.get("reason") or "").strip()
            if not keyword or keyword in false_gaps:
                continue
            if any(keyword in (f.get("title") or "") for f in kept_gaps):
                continue
            kept_gaps.append(
                {
                    "category": "gap",
                    "severity": "red" if gap.get("priority") == "high" else "yellow",
                    "title": f"岗位要求「{keyword}」在简历中缺少对应证据",
                    "original_text": "",
                    "issue": reason or f"JD 要求「{keyword}」，简历未给出可对应的经历或成果。",
                    "suggestion": f"若具备相关经验，请补充一条以「{keyword}」为核心动作的经历；若不具备，用相邻能力做迁移表述说明可快速上手。",
                    "rewritten_text": f"{keyword}：[补充一条以它为主语的经历，写清规模与结果]",
                    "evidence": {"source": "jd", "quote": keyword if keyword in ctx.jd_text else "", "span": None, "field": "parsed.skills_core", "unverified": keyword not in ctx.jd_text},
                }
            )

        findings = merge_findings(kept_gaps, llm_gaps)
        ctx.add_findings(findings)
        ctx.missing_keywords = [kw for kw in missing if kw not in false_gaps]
        ctx.covered_keywords = covered

        match_summary = str(data.get("match_summary") or baseline_summary).strip()
        output = {
            "match_summary": match_summary,
            "missing_keywords": ctx.missing_keywords,
            "covered_keywords": covered,
            "coverage": round(ratio, 4),
            "false_gaps": sorted(false_gaps),
            "findings_count": len(findings),
            "coverage_comment": str(data.get("coverage_comment") or "").strip(),
        }
        ctx.outputs[self.name] = output

        return AgentOutput(
            data=output,
            llm=result,
            status="degraded" if result.degraded else "done",
            notes=result.note,
        )


def _suppressed_by(finding: dict, false_gaps: set[str]) -> bool:
    """判断一条发现项是否命中"表述差异"清单。"""
    if not false_gaps:
        return False
    haystack = f"{finding.get('title', '')} {finding.get('issue', '')} {finding.get('original_text', '')}"
    return any(kw and kw in haystack for kw in false_gaps)


def _resume_digest(ctx: AgentContext, limit: int = 6) -> str:
    lines: list[str] = []
    for w in ctx.parsed_resume.work[:3]:
        lines.append(f"- {w.company} | {w.title} | {w.start}-{w.end}")
        for h in w.highlights[:3]:
            lines.append(f"  · {h[:100]}")
    for p in ctx.parsed_resume.projects[:2]:
        lines.append(f"- 项目：{p.name} | {p.role}")
        for h in p.highlights[:2]:
            lines.append(f"  · {h[:100]}")
    return "\n".join(lines[:limit * 3]) or "（未解析出结构化经历）"
