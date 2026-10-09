"""报告合成 Agent：五维评分、风险等级、总结与知识库引用。

评分由规则层计算（`analyze.compute_scores`），不让 LLM 打分 —— 理由：
同一份简历两次诊断必须给出同样的分数，否则「修改后重新诊断，对比分数变化」这一卖点就是假的。
LLM 只负责写总结（summary）。
"""

from __future__ import annotations

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput

DISCLAIMER = "本报告由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。"


class ReportAgent(Agent):
    name = "report"
    title = "报告合成 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        scores = analyze.compute_scores(ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        risk_level = analyze.risk_level_from_score(scores["risk"])
        breakdown = {sev: len([f for f in ctx.findings if f["severity"] == sev]) for sev in ("red", "yellow", "green")}

        ctx.scores = scores
        ctx.knowledge_refs = [
            {
                "doc_id": hit.get("doc_id"),
                "chunk_id": hit.get("chunk_id"),
                "title": hit.get("title") or "",
                "scope": hit.get("scope") or "",
                "score": hit.get("score"),
            }
            for hit in ctx.knowledge_hits[:3]
        ]

        baseline_summary = _compose_summary(ctx, scores, risk_level, breakdown)

        def mock_factory() -> dict:
            return {"summary": baseline_summary, "highlights": _highlights(ctx)}

        user = (
            f"【目标岗位】{ctx.parsed_jd.title}\n"
            f"【五维得分】综合 {scores['overall']}｜ATS {scores['ats']}｜影响力 {scores['impact']}｜"
            f"清晰度 {scores['clarity']}｜竞争力 {scores['match']}｜风险安全度 {scores['risk']}（风险等级 {risk_level}）\n"
            f"【问题分布】红 {breakdown['red']} / 黄 {breakdown['yellow']} / 绿 {breakdown['green']}\n"
            f"【最严重的问题】\n{_worst(ctx)}\n\n"
            f"请写一段 150 字以内的总结：先给整体判断，再点出最关键的一个问题，最后给一句正向的行动指引。"
            "不要复述分数，不要罗列所有问题。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("report_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        summary = str(result.data.get("summary") or baseline_summary).strip()
        ctx.summary = summary

        output = {
            "scores": scores,
            "risk_level": risk_level,
            "severity_breakdown": breakdown,
            "summary": summary,
            "knowledge_refs": ctx.knowledge_refs,
            "disclaimer": DISCLAIMER,
        }
        ctx.outputs[self.name] = output

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=result.note)


def _compose_summary(ctx: AgentContext, scores: dict, risk_level: str, breakdown: dict) -> str:
    """确定性总结：live 失败时也不会让报告缺字段。"""
    level_text = {"green": "风险较低", "yellow": "存在若干需要处理的风险点", "red": "存在明显风险，建议优先修正"}[risk_level]
    top = next((f for f in ctx.findings if f["severity"] == "red"), None) or (ctx.findings[0] if ctx.findings else None)
    parts = [
        f"综合得分 {scores['overall']} 分，与岗位「{ctx.parsed_jd.title or '目标岗位'}」的匹配度处于中等水平，{level_text}。",
    ]
    if top:
        parts.append(f"最需要优先处理的是「{top['title']}」：{top['issue'][:70]}")
    else:
        parts.append("未发现明显硬伤，可从量化成果与关键词对齐两方面继续打磨。")
    parts.append(
        f"共 {breakdown['red']} 项高风险、{breakdown['yellow']} 项待优化，按行动清单逐条修改后可再诊断一次对比分数变化。"
    )
    return "".join(parts)[:300]


def _highlights(ctx: AgentContext) -> list[str]:
    return [f["title"] for f in ctx.findings if f["severity"] == "green"][:3]


def _worst(ctx: AgentContext) -> str:
    for f in ctx.findings:
        if f["severity"] == "red":
            return f"[{f['category']}] {f['title']}\n问题：{f['issue'][:200]}\n原文：{(f.get('original_text') or '')[:120]}"
    return "（无红色风险）"
