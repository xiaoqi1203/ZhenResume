"""ATS Agent：以「机器能否读懂」的视角审查简历可解析性与关键词布局。

规则层负责确定性检测（缺技能栏、无联系方式、表格线、篇幅），
LLM 负责补充版式与措辞层面的可解析性问题。两者合并去重。
"""

from __future__ import annotations

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .finding_utils import coerce_findings, merge_findings


class AtsAgent(Agent):
    name = "ats"
    title = "ATS Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        baseline = coerce_findings(
            analyze.ats_findings(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text, ctx.jd_text),
            default_category="ats",
            ctx=ctx,
        )
        missing, covered = analyze.missing_keywords(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        total = len(missing) + len(covered)
        coverage = (len(covered) / total) if total else 0.0

        baseline_summary = (
            f"ATS 可解析性检查完成：识别到 {len(baseline)} 项问题；"
            f"关键词覆盖 {len(covered)}/{total}（{coverage:.0%}）。"
        )

        def mock_factory() -> dict:
            return {"findings": baseline, "ats_summary": baseline_summary}

        user = (
            f"【目标岗位】{ctx.parsed_jd.title}\n"
            f"【核心技能要求】{'、'.join(ctx.parsed_jd.skills_core) or '未识别'}\n"
            f"【简历已覆盖】{'、'.join(covered[:20]) or '无'}\n"
            f"【简历未覆盖】{'、'.join(missing[:15]) or '无'}\n\n"
            f"【简历技能栏】{'、'.join(ctx.parsed_resume.skills) or '未识别'}\n"
            f"【简历全文前 3000 字】\n{ctx.resume_text[:3000]}\n\n"
            f"【规则引擎初步发现】\n{_digest(baseline)}\n\n"
            "请确认、补充或剔除上述发现，确保每条都有原文引文，并给出一段 ATS 视角的总结。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("ats_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        llm_findings = coerce_findings(result.data.get("findings"), default_category="ats", ctx=ctx)
        findings = merge_findings(llm_findings, baseline)
        ctx.add_findings(findings)

        summary = str(result.data.get("ats_summary") or baseline_summary).strip()
        output = {
            "ats_summary": summary,
            "coverage": round(coverage, 4),
            "findings_count": len(findings),
        }
        ctx.outputs[self.name] = output

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=result.note)


def _digest(findings: list[dict], limit: int = 8) -> str:
    lines = [f"{i + 1}. [{f['severity']}] {f['title']} ｜ 引文：{(f.get('evidence') or {}).get('quote', '')[:60]}" for i, f in enumerate(findings[:limit])]
    return "\n".join(lines) or "（无）"
