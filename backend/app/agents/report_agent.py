"""报告合成 Agent：五维评分、风险等级、总结与知识库引用。

评分由规则层计算（`analyze.compute_scores_deterministic`），不让 LLM 打分 —— 理由：
同一份简历两次诊断必须给出同样的分数，否则「修改后重新诊断，对比分数变化」这一卖点就是假的。
LLM 只负责写总结（summary）。

⚠️ **定稿口径只有一处**（`analyze.finalize_report`）：
本 Agent 不再自己调 `compute_scores`，也不自己拼摘要里的数字。
历史上这里算过一次、落库时又算了一次，导致报告页同时出现 `overall=85` 和
「综合得分 84 分」（sample_resume_2_medium，必现）。现在分数只算一次。
"""

from __future__ import annotations

import re

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput

DISCLAIMER = "本报告由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。"


class ReportAgent(Agent):
    name = "report"
    title = "报告合成 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        # 唯一算分入口：分数 / risk_level / 红黄计数都出自同一次计算
        finalized = analyze.finalize_report(
            parsed_resume=ctx.parsed_resume,
            parsed_jd=ctx.parsed_jd,
            role_family=ctx.role_family,
            raw_text=ctx.resume_text,
            jd_text=ctx.jd_text,
            final_findings=ctx.findings,
        )
        scores = finalized["scores"]
        risk_level = finalized["risk_level"]
        breakdown = finalized["severity_breakdown"]

        ctx.scores = scores
        ctx.risk_level = risk_level  # 让 finalize / persist 复用同一结论
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

        baseline_summary = _compose_summary(ctx, scores, risk_level, breakdown, finalized["top_finding"])

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
            # 模型一旦复述分数就极易与 scores 打架（它会把 ATS/影响力 当成综合分）。
            # 这里明确禁止，再由下方 _guard_summary 做最后一道兜底。
            "**严禁在总结里写任何数字分数**（例如「综合得分 84 分」），分数一律由报告卡片展示。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("report_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        summary = _guard_summary(str(result.data.get("summary") or "").strip(), baseline_summary, scores)
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


def _compose_summary(
    ctx: AgentContext, scores: dict, risk_level: str, breakdown: dict, top_finding: dict | None
) -> str:
    """确定性总结：live 失败时也不会让报告缺字段。

    实现委托给 `analyze.compose_summary` —— 摘要里的每个数字与 `scores` 同源，
    不再各处各拼一份（这正是 85 vs 84 那类矛盾的温床）。
    """
    return analyze.compose_summary(
        scores=scores,
        risk_level=risk_level,
        breakdown=breakdown,
        top_finding=top_finding,
        job_title=ctx.parsed_jd.title or "",
    )


# 「综合得分 84 分」/「总分 84」这类以分数开头的表述：模型复述时最容易和 scores 打架
_SUMMARY_SCORE_RE = re.compile(r"(综合得分|总分|综合分数)\s*(\**)(\d{1,3})(\**)\s*分")


def _guard_summary(summary: str, baseline: str, scores: dict) -> str:
    """兜底：模型写出的总分与 `scores` 不一致时，丢弃模型文本改用确定性总结。

    只处理"模型自己报了一个不同的总分"这一种冲突；正常描述（不含数字分数）原样保留。
    """
    if not summary:
        return baseline
    overall = int(scores.get("overall") or 0)
    mismatched = any(int(m.group(3)) != overall for m in _SUMMARY_SCORE_RE.finditer(summary))
    if mismatched:
        return baseline
    return summary


def _highlights(ctx: AgentContext) -> list[str]:
    return [f["title"] for f in ctx.findings if f["severity"] == "green"][:3]


def _worst(ctx: AgentContext) -> str:
    for f in ctx.findings:
        if f["severity"] == "red":
            return f"[{f['category']}] {f['title']}\n问题：{f['issue'][:200]}\n原文：{(f.get('original_text') or '')[:120]}"
    return "（无红色风险）"
