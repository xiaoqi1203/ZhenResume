"""影响力 Agent：量化成果与强动词审查。

规则层判定「是否含数字指标 / 是否用弱动词开头」，这是客观事实；
LLM 负责把弱表述改写成可直接复制的结果导向句（不编造数字，缺数据的位置用 [补充指标：…] 占位）。
"""

from __future__ import annotations

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .finding_utils import coerce_findings, merge_findings


class ImpactAgent(Agent):
    name = "impact"
    title = "影响力 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        baseline = coerce_findings(
            analyze.impact_findings(ctx.parsed_resume, ctx.role_family, ctx.resume_text),
            default_category="impact",
            ctx=ctx,
        )

        bullets = analyze.content_bullets(ctx.parsed_resume)
        quantified = [t for _, t in bullets if analyze._is_quantified(t)]
        quant_ratio = (len(quantified) / len(bullets)) if bullets else 0.0
        hints = analyze.METRIC_HINTS.get(ctx.role_family, analyze.METRIC_HINTS["general"])

        baseline_summary = (
            f"共 {len(bullets)} 条经历要点，其中 {len(quantified)} 条含量化指标（{quant_ratio:.0%}）。"
            + ("量化严重不足，这是最优先要改的地方。" if quant_ratio < 0.3 else "量化程度尚可，可继续在关键经历上加码。")
        )

        def mock_factory() -> dict:
            return {"findings": baseline, "impact_summary": baseline_summary}

        user = (
            f"【岗位族】{ctx.role_family}，该岗位常见的量化维度：{'、'.join(hints)}\n"
            f"【经历要点清单】\n{_bullets_digest(bullets)}\n\n"
            f"【规则引擎初步发现】\n{_digest(baseline)}\n\n"
            f"【岗位知识库依据】\n{ctx.knowledge_digest()}\n\n"
            "请补充/修正发现项，并为最需要改写的 3–5 条经历给出结果导向的改写句。"
            "严禁编造具体数字：缺失数据一律写成 [补充指标：…]。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("impact_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        llm_findings = coerce_findings(result.data.get("findings"), default_category="impact", ctx=ctx)
        findings = merge_findings(llm_findings, baseline)
        ctx.add_findings(findings)

        summary = str(result.data.get("impact_summary") or baseline_summary).strip()
        output = {
            "impact_summary": summary,
            "bullet_count": len(bullets),
            "quantified_count": len(quantified),
            "quantified_ratio": round(quant_ratio, 4),
            "metric_hints": hints,
            "findings_count": len(findings),
        }
        ctx.outputs[self.name] = output

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=result.note)


def _bullets_digest(bullets: list[tuple[str, str]], limit: int = 18) -> str:
    if not bullets:
        return "（未解析出经历要点，请基于全文自行判断）"
    lines = []
    for i, (field, text) in enumerate(bullets[:limit], 1):
        mark = "有数字" if analyze._is_quantified(text) else "无数字"
        lines.append(f"{i}. [{mark}] ({field}) {text[:160]}")
    return "\n".join(lines)


def _digest(findings: list[dict], limit: int = 8) -> str:
    return "\n".join(f"{i + 1}. [{f['severity']}] {f['title']} ｜ {(f.get('original_text') or '')[:70]}" for i, f in enumerate(findings[:limit])) or "（无）"
