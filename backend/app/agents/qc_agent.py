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
    r"(性别|男性优先|女性优先|限男|限女|已婚|未婚|婚育|生育|年龄限制|超过\d+岁|"
    r"年龄|户籍|本地户口|身高|体重|外貌|形象气质|籍贯|民族|"
    # 裸年龄表述（`35 岁` / `28周岁`）：简历里的年岁几乎总是敏感信息，
    # 且"按 XX 岁判断适配"是典型歧视句式，所以这里也要能命中
    r"\d{2}\s*(?:岁|周岁))"
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
        kept = _drop_unlocatable_red(kept, ctx)

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
        # 报告定稿（分数 / 风险等级 / 摘要数字）在 qc 之后、用**最终清单**算一次。
        # 放在这里而不是各引擎里，是为了让 legacy / LangGraph 两条轨道共用同一份结论 ——
        # 否则「3 条红 + risk_level=yellow」会在某一轨道上被重新写回。
        _finalize(ctx)

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
# 定稿：报告级数字在这里一次算完
# --------------------------------------------------------------------------- #
def _finalize(ctx: AgentContext) -> None:
    """用**质检后的最终清单**定稿分数、风险等级与摘要里的数字。

    这一步在 qc 内部完成，而不是交给引擎，原因：
    legacy（engine.py）、LangGraph 串行、LangGraph 并行三条轨道都要用同一份结论，
    放在 Agent 里只有一处实现，不会出现"某条轨道忘了重算"。

    注意 `ctx.summary` 只有在模型总结与分数冲突时才会被替换（见 report_agent._guard_summary），
    这里只重写"由确定性模板生成的那种摘要" —— 判据是摘要以「综合得分」开头。
    """
    finalized = analyze.finalize_report(
        parsed_resume=ctx.parsed_resume,
        parsed_jd=ctx.parsed_jd,
        role_family=ctx.role_family,
        raw_text=ctx.resume_text,
        jd_text=ctx.jd_text,
        final_findings=ctx.findings,
        # 双分制：只有 agentic 模式出深度分。规则分不受影响。
        include_depth=str(ctx.workflow_mode or "").lower() == "agentic",
    )
    ctx.scores = finalized["scores"]
    ctx.risk_level = finalized["risk_level"]
    if "depth_scores" in finalized:
        ctx.depth_scores = finalized["depth_scores"]

    deterministic = analyze.compose_summary(
        scores=finalized["scores"],
        risk_level=finalized["risk_level"],
        breakdown=finalized["severity_breakdown"],
        top_finding=finalized["top_finding"],
        job_title=ctx.parsed_jd.title or "",
    )
    if not ctx.summary or ctx.summary.startswith("综合得分"):
        ctx.summary = deterministic
        return
    # 模型写的总结：只修"总分数字与 scores 打架"这一种情况，别的原样保留。
    ctx.summary = _align_summary_score(ctx.summary, finalized["scores"]["overall"]) or deterministic


_SUMMARY_SCORE_RE = re.compile(r"(综合得分|总分|综合分数)(\s*\**\s*)(\d{1,3})(\s*\**\s*)分")


def _align_summary_score(summary: str, overall: int) -> str:
    """把总结里自报的总分改写为 `overall`；没有自报总分则原样返回。"""
    if not _SUMMARY_SCORE_RE.search(summary):
        return summary
    return _SUMMARY_SCORE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{overall}{m.group(4)}分", summary)


def _drop_unlocatable_red(findings: list[dict], ctx: AgentContext) -> list[dict]:
    """兜底：红色条目若在**声明的来源**里定位不到，就不能留在红色。

    契约 §5.1 写死了「每条 red 的 quote 必须能在原文定位」，而 `_check` 的判据是
    「简历 / JD / 知识库任一处能定位」。两者差一步：声明 `source=jd` 的引文
    在简历里能搜到、在 JD 里搜不到时，`_check` 放行、契约却已经被违反。
    这一步按 source 严格复核，仍定位不到的降级为黄色（保留信息量，不静默删除）。
    """
    out: list[dict] = []
    for finding in findings:
        if finding.get("severity") != "red":
            out.append(finding)
            continue
        evidence = finding.get("evidence") or {}
        source = evidence.get("source") or "resume"
        quote = (evidence.get("quote") or "").strip()
        text = ctx.jd_text if source == "jd" else ctx.resume_text
        ok = bool(quote) and (source == "knowledge" or locate_quote(text, quote) is not None)
        if ok:
            out.append(finding)
            continue
        item = dict(finding)
        item["severity"] = "yellow"
        out.append(item)
    return out


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
    """判断**这条结论本身**是否在输出歧视性内容（而不是"指出简历里有敏感字段"）。

    ⚠️ 这里必须区分两种完全不同的情况（实测踩到的误杀）：
      A. 报告在**评价候选人**："35 岁不建议投递" / "已婚女性稳定性差" → 必须删除；
      B. 报告在**提醒删掉敏感字段**："简历中出现与岗位无关的敏感信息「性别」→ 建议删除"
         → 这是合规建议，**必须保留** —— 删掉它等于让用户继续留着违规内容。

    原实现只要命中敏感词就判 A，于是 B 类被一票否决。fixed 线上因为规则层很少主动
    产出这类条目而没暴露；agent 线的 risk Agent 会主动发现，于是 6 条里有 1 条被误删、
    公平性通过率被拉低（实测 0.8571 / 0.9966），还让用户看不到「删掉性别字段」这个正确提醒。

    判据：出现敏感词 **且** 句子在替候选人做能力/适配判断（带"适合/优先/劣势/扣分"等），
    才算 A。只出现"删除/不相关/合规/建议移除"这类词的是 B。
    """
    title = str(finding.get("title", ""))
    issue = str(finding.get("issue", ""))
    suggestion = str(finding.get("suggestion", ""))
    rewritten = str(finding.get("rewritten_text", ""))

    blob = f"{title} {issue} {suggestion} {rewritten}"
    if not DISCRIMINATION_RE.search(blob):
        return False

    # B 类：整条结论的语义就是"删除敏感字段" → 保留
    _REMOVAL_HINT = re.compile(
        r"(删除|移除|去掉|删除该|不再出现|无关|合规|不构成|不便|避免)|"
        r"(建议.{0,6}(删|移|去))"
    )
    if _REMOVAL_HINT.search(blob) and not _capability_judgement(blob):
        return False

    # A 类：拿敏感字段判断能力/适配 → 删除
    return _capability_judgement(blob)


_CAPABILITY_JUDGE_RE = re.compile(
    # 敏感字段 + 能力/适配判断（词序：字段在前）
    r"(适合|不适合|优先|劣势|优势|扣分|加分|稳定性差|不建议|不予考虑|不予录用|风险高|偏大|偏小|过大)"
    r".{0,12}"
    r"(男|女|年龄|已婚|未婚|婚育|户籍|本地户口|身高|体重|外貌|形象|籍贯|民族|\d{2}\s*岁)"
    r"|"
    # 字段在前、判断在后
    r"(男|女|年龄|已婚|未婚|婚育|户籍|本地户口|身高|体重|外貌|形象|籍贯|民族|\d{2}\s*岁)"
    r".{0,12}"
    r"(适合|不适合|优先|劣势|优势|扣分|加分|不宜|不建议|不予|稳定性差|偏大|偏小)"
)


def _capability_judgement(text: str) -> bool:
    """是否在拿敏感字段评价候选人（而不是提醒删除）。"""
    return bool(_CAPABILITY_JUDGE_RE.search(text))


def _check(index: int, finding: dict, ctx: AgentContext) -> dict:
    severity = finding.get("severity", "yellow")
    evidence = finding.get("evidence") or {}

    # ① 歧视性内容 —— 一票否决，直接删
    #
    # 注意 `_looks_discriminatory` 会放行"提醒删除敏感字段"这类合规建议（见其 docstring）。
    if _looks_discriminatory(finding):
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
