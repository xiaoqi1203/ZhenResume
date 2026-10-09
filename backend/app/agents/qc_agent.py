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
            "请逐条判断：引文是否真实存在、改写句是否可执行、是否含歧视性内容，"
            "给出 keep / downgrade / drop 及理由。不确定时选择 keep。"
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
            if action == "drop":
                violations.append({"index": idx, "action": "drop", "reason": f"LLM 质检：{reason or '不合格'}", "rule": "llm"})
                kept[idx] = None  # type: ignore[assignment]
                dropped += 1
            elif action == "downgrade":
                item = kept[idx]
                if item and item["severity"] != "green":
                    item["severity"] = SEVERITY_DOWNGRADE[item["severity"]]
                    downgraded += 1
                    violations.append({"index": idx, "action": "downgrade", "reason": f"LLM 质检：{reason or '建议降级'}", "rule": "llm"})

        kept = [f for f in kept if f]
        kept = _ensure_minimum(kept, ctx)
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
def _check(index: int, finding: dict, ctx: AgentContext) -> dict:
    severity = finding.get("severity", "yellow")
    evidence = finding.get("evidence") or {}
    quote = (evidence.get("quote") or "").strip()
    text_blob = f"{finding.get('title', '')} {finding.get('issue', '')} {finding.get('suggestion', '')} {finding.get('rewritten_text', '')}"

    # ① 歧视性内容 —— 一票否决，直接删
    if DISCRIMINATION_RE.search(text_blob):
        return {"index": index, "action": "drop", "rule": "discrimination", "reason": "输出含性别/年龄/婚育等敏感内容，已拦截"}

    # ② 红色风险必须有可定位引文
    if severity == "red":
        locatable = bool(quote) and (
            locate_quote(ctx.resume_text, quote) is not None
            or locate_quote(ctx.jd_text, quote) is not None
            or evidence.get("source") == "knowledge"
        )
        if not locatable:
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
