"""质检 Agent：报告出厂前的最后一道闸门。

按 aaa.md §五.3 的四项要求逐条校验：
    ① 是否有原文依据（红色风险必须有可定位引文）
    ② 是否含歧视性内容
    ③ 是否给出可执行建议
    ④ 是否引用知识库来源

动作：不合格条目 keep / downgrade / drop。降级比删除优先 —— 保留信息量，
但绝不让「无证据的红色风险」出现在报告里。
质检是流水线最后一步，它会改动 findings，因此引擎会在 qc 之后**重算分数**，
否则总分与最终展示的问题清单会不一致。
"""

from __future__ import annotations

import re

from ..services import analyze
from ..services.evidence import locate_quote
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .suggestion_agent import _ensure_minimum

DISCRIMINATION_RE = re.compile(
    r"(性别|男性优先|女性优先|限男|限女|已婚|未婚|婚育|生育|年龄限制|超过\d+岁|户籍|本地户口|身高|体重|外貌|形象气质|籍贯|民族)"
)

SEVERITY_DOWNGRADE = {"red": "yellow", "yellow": "green", "green": "green"}


class QcAgent(Agent):
    name = "qc"
    title = "质检 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        kept: list[dict] = []
        violations: list[dict] = []
        dropped = 0
        downgraded = 0
        evaluated = len(ctx.findings)

        for index, finding in enumerate(ctx.findings):
            verdict = _check(index, finding, ctx)
            if verdict["action"] == "drop":
                dropped += 1
                violations.append(verdict)
                continue
            if verdict["action"] == "downgrade":
                downgraded += 1
                violations.append(verdict)
                finding = dict(finding)
                finding["severity"] = SEVERITY_DOWNGRADE[finding["severity"]]
            kept.append(finding)

        # 二次确认：LLM 复核（mock 模式下无决策，不影响结果）
        def mock_factory() -> dict:
            return {"decisions": [], "fairness_comment": "规则质检通过"}

        user = (
            f"【待质检的发现项（索引从 0 开始）】\n{_digest(kept)}\n\n"
            f"【简历原文前 2500 字（用于核对引文真实性）】\n{ctx.resume_text[:2500]}\n\n"
            f"【JD 原文（引文也可能来自这里 —— 「关键词缺失」类结论的引文就是关键词本身）】\n{ctx.jd_text[:1200]}\n\n"
            "请逐条判断：引文是否真实存在、改写句是否可执行、是否含歧视性内容，"
            "给出 keep / downgrade / drop 及理由。不确定时选择 keep。\n"
            "重要：引文可以来自**简历原文**、**JD 原文**或**知识库**三者之一 —— "
            "只要在其中任意一处能找到，就不算「引文不真实」，不要因此 drop。\n"
            "重要：改写句里的 `[补充：…]`、`[补充指标：…]` 这类**占位符是设计要求**"
            "（缺数据时禁止编造数字与经历），不构成「不可执行」，不要因此 drop 或 downgrade。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("qc_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        decisions = result.data.get("decisions") if isinstance(result.data.get("decisions"), list) else []
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            try:
                idx = int(decision.get("index"))
            except (TypeError, ValueError):
                continue
            action = str(decision.get("action") or "").lower()
            reason = str(decision.get("reason") or "").strip()
            if idx < 0 or idx >= len(kept):
                continue
            item = kept[idx]
            if action == "drop":
                # **模型的"引文不真实"判断不能推翻确定性校验**。
                # 实测：qc 的 LLM 会把所有「关键词缺失」类红色条目全删掉，理由是
                # 「引文 Java 不在简历原文中」—— 但这些引文本来就来自 JD，
                # 而规则层 `_check` 明确接受"能在简历或 JD 中定位"。
                # 结果是一条红色风险都不剩（verify 的「存在红色风险」当场失败）。
                # 原则与"证据链不信任模型定位"一致：定位由 locate_quote 说了算。
                if item is not None and _evidence_locatable(item, ctx) and not _looks_discriminatory(item):
                    violations.append({
                        "index": idx, "action": "keep", "rule": "llm_vetoed",
                        "reason": f"模型建议删除但引文可在原文定位，已驳回：{reason[:80]}",
                    })
                    continue
                violations.append({"index": idx, "action": "drop", "reason": f"LLM 质检：{reason or '不合格'}", "rule": "llm"})
                kept[idx] = None  # type: ignore[assignment]
                dropped += 1
            elif action == "downgrade":
                # 同理：**规则层条目的严重度不由模型改**。
                # 实测：qc 的 LLM 会把所有「关键词缺失」红色条目降级成黄色，理由是
                # 改写句是「Java：[补充一条以它为主语的经历]」这种**占位符**、"不可直接复制"。
                # 但占位符恰恰是本项目的设计要求（缺数据时禁止编造数字/经历），
                # 结果是一条红色都不剩。规则层自己的 `_check` 已经判过可执行性，
                # 模型不得推翻 —— 否则"同一份简历两次诊断"连展示的严重度都会变。
                if item is not None and item.get("origin", "rule") == "rule":
                    violations.append({
                        "index": idx, "action": "keep", "rule": "llm_vetoed",
                        "reason": f"模型建议降级但该条为规则层确定性结论，已驳回：{reason[:80]}",
                    })
                    continue
                if item and item["severity"] != "green":
                    item["severity"] = SEVERITY_DOWNGRADE[item["severity"]]
                    downgraded += 1
                    violations.append({"index": idx, "action": "downgrade", "reason": f"LLM 质检：{reason or '建议降级'}", "rule": "llm"})

        kept = [f for f in kept if f]
        kept = _ensure_minimum(kept, ctx)
        # 质检后补进来的条目由 suggestion 内部生成，属确定性内容；补来源标记，
        # 保证"评分只认规则层"这条规则对它们成立（见 analyze.compute_scores）。
        for item in kept:
            item.setdefault("origin", "rule")
        kept = analyze.sort_findings(kept)

        evaluated = max(evaluated, 1)
        fairness_pass_rate = 1.0 - (len([v for v in violations if v.get("rule") == "discrimination"]) / evaluated)
        fairness_pass_rate = max(0.0, min(1.0, fairness_pass_rate))

        ctx.findings = kept
        ctx.qc_report = {
            "passed": not any(v["action"] == "drop" for v in violations),
            "evaluated": evaluated,
            "dropped": dropped,
            "downgraded": downgraded,
            "fairness_pass_rate": round(fairness_pass_rate, 4),
            "violations": violations[:10],
        }

        output = {
            **ctx.qc_report,
            "final_findings_count": len(kept),
            "severity_breakdown": {sev: len([f for f in kept if f["severity"] == sev]) for sev in ("red", "yellow", "green")},
            "knowledge_refs_count": len(ctx.knowledge_refs),
        }
        ctx.outputs[self.name] = output

        notes = result.note
        if dropped or downgraded:
            extra = f"质检调整：剔除 {dropped} 条、降级 {downgraded} 条。"
            notes = f"{notes} | {extra}" if notes else extra

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=notes)


# --------------------------------------------------------------------------- #
# 规则质检
# --------------------------------------------------------------------------- #
def _evidence_locatable(finding: dict, ctx: AgentContext) -> bool:
    """引文是否能在简历 / JD / 知识库中定位（与 `_check` 的判据保持同一套）。"""
    evidence = finding.get("evidence") or {}
    quote = (evidence.get("quote") or "").strip()
    if not quote:
        return False
    return bool(
        locate_quote(ctx.resume_text, quote) is not None
        or locate_quote(ctx.jd_text, quote) is not None
        or evidence.get("source") == "knowledge"
    )


def _looks_discriminatory(finding: dict) -> bool:
    blob = f"{finding.get('title', '')} {finding.get('issue', '')} {finding.get('suggestion', '')} {finding.get('rewritten_text', '')}"
    return bool(DISCRIMINATION_RE.search(blob))


def _check(index: int, finding: dict, ctx: AgentContext) -> dict:
    severity = finding.get("severity", "yellow")
    evidence = finding.get("evidence") or {}
    text_blob = f"{finding.get('title', '')} {finding.get('issue', '')} {finding.get('suggestion', '')} {finding.get('rewritten_text', '')}"

    # ① 歧视性内容 —— 一票否决，直接删
    if DISCRIMINATION_RE.search(text_blob):
        return {"index": index, "action": "drop", "rule": "discrimination", "reason": "输出含性别/年龄/婚育等敏感内容，已拦截"}

    # ② 红色风险必须有可定位引文（简历 / JD / 知识库任一处均可）
    if severity == "red":
        if not _evidence_locatable(finding, ctx):
            quote = (evidence.get("quote") or "").strip()
            return {
                "index": index,
                "action": "downgrade",
                "rule": "no_evidence",
                "reason": f"红色风险缺少可定位原文引文（quote={'空' if not quote else quote[:30]}），降级为黄色",
            }

    # ③ 可执行建议
    if not str(finding.get("rewritten_text") or "").strip() or not str(finding.get("suggestion") or "").strip():
        return {"index": index, "action": "drop", "rule": "not_actionable", "reason": "缺少可复制的改写句或具体建议"}

    # ④ 知识库引用：报告级检查，无引用时不拦截，仅记录
    return {"index": index, "action": "keep", "rule": "ok", "reason": ""}


def _digest(findings: list[dict], limit: int = 12) -> str:
    lines = []
    for i, f in enumerate(findings[:limit]):
        lines.append(
            f"{i}. [{f['severity']}/{f['category']}] {f['title']}\n"
            f"   引文：{((f.get('evidence') or {}).get('quote') or '（无）')[:80]}\n"
            f"   改写：{(f.get('rewritten_text') or '')[:80]}"
        )
    return "\n".join(lines) or "（无）"
