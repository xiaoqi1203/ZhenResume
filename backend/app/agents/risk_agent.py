"""风险 Agent：时间线、矛盾、夸大表述与合规敏感信息。

这一层同时承担合规职责（aaa.md §4「公平性：不输出性别、年龄、婚育等歧视性建议」）：
规则层用 DISCRIMINATION_RE 扫描简历与 JD 中的敏感字段，
并在此明确要求 LLM 不得基于这些字段做任何能力判断。
"""

from __future__ import annotations

import re

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .finding_utils import coerce_findings, merge_findings


class RiskAgent(Agent):
    name = "risk"
    title = "风险 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        baseline = coerce_findings(
            analyze.risk_findings(ctx.parsed_resume, ctx.resume_text),
            default_category="risk",
            ctx=ctx,
        )

        timeline = ctx.parsed_resume.timeline_entries()
        timeline_digest = "\n".join(f"- {s} ~ {e}  {label}" for s, e, label in timeline) or "（未解析到时间线）"
        sensitive_resume = sorted({m.group(0) for m in analyze.DISCRIMINATION_RE.finditer(ctx.resume_text or "")})
        sensitive_jd = sorted({m.group(0) for m in analyze.DISCRIMINATION_RE.finditer(ctx.jd_text or "")})

        baseline_summary = (
            f"检测到 {len(baseline)} 项风险。"
            + (f"其中时间线相关 {len([f for f in baseline if '时间' in f['title'] or '空窗' in f['title'] or '重叠' in f['title']])} 项。" if baseline else "未发现明显时间线断层或矛盾。")
        )

        def mock_factory() -> dict:
            return {"findings": baseline, "risk_summary": baseline_summary}

        user = (
            f"【时间线（起 ~ 止  说明）】\n{timeline_digest}\n\n"
            f"【工作经历条数与项目数】{len(ctx.parsed_resume.work)} 段工作 / {len(ctx.parsed_resume.projects)} 个项目\n"
            f"【技能栏】{'、'.join(ctx.parsed_resume.skills) or '未识别'}\n"
            f"【项目技术】{'、'.join(sorted({t for p in ctx.parsed_resume.projects for t in p.tech})) or '无'}\n\n"
            f"【简历中出现的敏感字段】{'、'.join(sensitive_resume) or '无'}\n"
            f"【JD 中出现的敏感字段】{'、'.join(sensitive_jd) or '无'}\n\n"
            f"【简历全文前 3000 字】\n{ctx.resume_text[:3000]}\n\n"
            f"【规则引擎初步发现】\n{_digest(baseline)}\n\n"
            "请补充或修正风险项。注意：敏感字段只能作为「建议删除以规避合规争议」提出，"
            "绝不能据此评价候选人能力或匹配度。每条风险要说明 HR 会如何解读。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("risk_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        llm_findings = coerce_findings(result.data.get("findings"), default_category="risk", ctx=ctx)
        llm_findings = [f for f in llm_findings if not _is_discriminatory(f)]
        findings = merge_findings(llm_findings, baseline)
        ctx.add_findings(findings)

        summary = str(result.data.get("risk_summary") or baseline_summary).strip()
        output = {
            "risk_summary": summary,
            "timeline_count": len(timeline),
            "sensitive_fields_resume": sensitive_resume,
            "sensitive_fields_jd": sensitive_jd,
            "findings_count": len(findings),
        }
        ctx.outputs[self.name] = output

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=result.note)


def _is_discriminatory(finding: dict) -> bool:
    """兜底过滤：任何把敏感字段当作能力判断依据的条目一律剔除。"""
    text = f"{finding.get('issue', '')} {finding.get('suggestion', '')} {finding.get('title', '')}"
    bad = re.compile(r"(适合|不适合|优先|劣势|优势|扣分|加分).{0,6}(男|女|年龄|已婚|未婚|婚育|户籍|本地户口)")
    return bool(bad.search(text))


def _digest(findings: list[dict], limit: int = 8) -> str:
    return "\n".join(f"{i + 1}. [{f['severity']}] {f['title']}" for i, f in enumerate(findings[:limit])) or "（无）"
