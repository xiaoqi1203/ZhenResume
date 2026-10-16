"""HR 排序表专用节点（轻量筛选）。

**为什么单独一套节点，而不是复用完整流水线**：
详细分析（impact 量化改写、suggestion 改写建议、report 求职者总结）是给**求职者**的，
HR 初筛只要"谁更匹配、有什么风险"。实测这三种节点占了一半时间与成本：
    impact 15-23s / ¥0.008　suggestion 27-34s / ¥0.015　report 2-3s / ¥0.0015
砍掉它们之后，HR 侧每份从 8 次 LLM 调用降到 3 次（parse / jd_match / risk），
本文件里的 `hr_summary` 用**确定性文本**，不再额外调模型。

求职者侧的完整流水线不受影响：见 workflow/graph.py 的 build_graph()。
"""

from __future__ import annotations

from ..services import analyze
from .state import WorkflowState, ctx_from_state


def hr_summary_node(state: WorkflowState) -> dict:
    """HR 视角的确定性摘要 + 知识库引用（**不调 LLM**）。

    内容对齐 `aaa.md §3.9` 的"简版报告"：匹配度 → 命中/缺口 → 风险提示。
    之所以不调模型：
    - 这些数字全部来自规则层，模型只会改写措辞，却要多花 ~10s 和 ¥0.006/份；
    - 排序表要的是"可比较、可复现"，措辞花哨反而干扰 HR 横向对比。
    需要更"像人写"的摘要时，再单独开一个 `HR_SUMMARY_LLM=true` 开关走 LLM。
    """
    ctx = ctx_from_state(state)
    scores = ctx.scores or {}
    risk_level = analyze.risk_level_from_score(int(scores.get("risk") or 0))

    refs = [
        {
            "doc_id": hit.get("doc_id"),
            "chunk_id": hit.get("chunk_id"),
            "title": hit.get("title") or "",
            "scope": hit.get("scope") or "",
            "score": hit.get("score"),
        }
        for hit in ctx.knowledge_hits[:3]
    ]

    summary = _compose_hr_summary(ctx, scores, risk_level)

    output = {
        "scores": scores,
        "risk_level": risk_level,
        "summary": summary,
        "knowledge_refs": refs,
        # HR 关心的四段（契约 §6.5 的 items 目前只暴露 summary，这四个键留给后续扩展）
        "match_evidence": _match_evidence(ctx),
        "gaps": ctx.missing_keywords[:8],
        "risk_items": _risk_items(ctx),
    }
    return {
        "summary": summary,
        "knowledge_refs": refs,
        "outputs": {"hr_summary": output},
    }


def _compose_hr_summary(ctx, scores: dict, risk_level: str) -> str:
    """HR 视角摘要：匹配度 → 命中/缺口 → 风险提示。全部来自确定性数据。"""
    total = len(ctx.missing_keywords) + len(ctx.covered_keywords)
    hit = len(ctx.covered_keywords)
    parts: list[str] = []

    if total:
        parts.append(f"岗位要求命中 {hit}/{total} 项")
    if scores:
        parts.append(f"匹配度 {scores.get('match', '-')}/100、综合 {scores.get('overall', '-')}/100")

    if ctx.missing_keywords:
        parts.append(f"主要缺口：{'、'.join(ctx.missing_keywords[:4])}")
    else:
        parts.append("未发现关键词缺口")

    risks = _risk_items(ctx)
    if risks:
        parts.append(f"风险提示：{'；'.join(risks[:2])}")
    else:
        parts.append({"green": "未发现明显风险", "yellow": "有需关注项", "red": "存在明显风险"}[risk_level])

    if ctx.knowledge_refs:
        parts.append(f"依据：{ctx.knowledge_refs[0].get('title') or '岗位知识库'}")

    return "；".join(parts)[:300]


def _match_evidence(ctx, limit: int = 6) -> list[str]:
    """命中的 JD 要求（HR 最想看的一栏）。"""
    return [str(k) for k in ctx.covered_keywords[:limit]]


def _risk_items(ctx, limit: int = 4) -> list[str]:
    """风险类发现项（时间线/夸大/合规），按严重度排序后取前几条。"""
    items = [f for f in ctx.findings if f.get("category") == "risk"]
    items = analyze.sort_findings(items)
    return [str(f.get("title") or "")[:60] for f in items[:limit]]
