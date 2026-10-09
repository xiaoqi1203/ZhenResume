"""解析 Agent：把简历纯文本结构化成 ParsedResume，并确保 JD 已解析。

critical = True：解析失败则整条链路没有意义，直接让任务 failed（前端会提示重试）。
策略：规则解析给出确定性基线 → LLM 纠错补全 → 逐字段合并
（LLM 字段为空时回落到基线，避免模型"越改越少"）。
"""

from __future__ import annotations

from ..schemas import ParsedResume
from ..services.llm import LLMClient, LLMResult
from ..services.parse_rules import guess_role_family, parse_jd_text, parse_resume_text
from .base import Agent, AgentContext, AgentOutput


def _merge_parsed(baseline: ParsedResume, candidate: dict) -> ParsedResume:
    """以 LLM 输出为主、规则基线兜底。"""
    try:
        llm_parsed = ParsedResume.model_validate(candidate)
    except Exception:  # noqa: BLE001 —— 模型结构不合法就整段回落到基线
        return baseline

    merged = llm_parsed.model_copy(deep=True)

    if not merged.basics.name:
        merged.basics.name = baseline.basics.name
    if not merged.basics.email:
        merged.basics.email = baseline.basics.email
    if not merged.basics.phone:
        merged.basics.phone = baseline.basics.phone
    if not merged.basics.city:
        merged.basics.city = baseline.basics.city
    if not merged.basics.links:
        merged.basics.links = baseline.basics.links

    for attr in ("education", "work", "projects"):
        if not getattr(merged, attr) and getattr(baseline, attr):
            setattr(merged, attr, getattr(baseline, attr))
    if not merged.skills:
        merged.skills = baseline.skills
    if not merged.certificates:
        merged.certificates = baseline.certificates
    if not merged.timeline:
        merged.timeline = baseline.timeline

    # 逐条经历兜底：LLM 漏掉的条目用基线补齐（按 (公司/学校, 时间) 去重）
    if baseline.work and len(merged.work) < len(baseline.work):
        known = {(w.company, w.start) for w in merged.work}
        for w in baseline.work:
            if (w.company, w.start) not in known:
                merged.work.append(w)

    return merged


class ParseAgent(Agent):
    name = "parse"
    title = "解析 Agent"
    critical = True

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        baseline = parse_resume_text(ctx.resume_text)

        def mock_factory() -> dict:
            return baseline.model_dump()

        user = (
            "【简历纯文本开始】\n"
            f"{ctx.resume_text[:12000]}\n"
            "【简历纯文本结束】\n\n"
            "【规则引擎初步结构化结果】\n"
            f"{baseline.model_dump_json(indent=None)}\n\n"
            "请输出纠正与补全后的完整 JSON。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("parse_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        parsed = _merge_parsed(baseline, result.data)
        ctx.parsed_resume = parsed

        # JD 若在创建时未解析出结构，这里补一次规则解析
        if not ctx.parsed_jd.title and not ctx.parsed_jd.skills_core:
            ctx.parsed_jd = parse_jd_text(ctx.jd_text)

        ctx.role_family = guess_role_family(ctx.parsed_jd)

        output = {
            "parsed_resume": parsed.model_dump(),
            "parsed_jd": ctx.parsed_jd.model_dump(),
            "role_family": ctx.role_family,
            "work_count": len(parsed.work),
            "project_count": len(parsed.projects),
            "bullet_count": len(parsed.bullets()),
            "skill_count": len(parsed.skills),
        }
        ctx.outputs[self.name] = output

        note = result.note
        if not parsed.work and not parsed.projects:
            note = (note + " | " if note else "") + "未从简历中解析出工作或项目经历，报告结论将主要基于全文文本。"

        return AgentOutput(
            data=output,
            llm=result,
            status="degraded" if result.degraded else "done",
            notes=note,
        )
