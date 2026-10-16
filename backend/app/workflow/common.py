"""工作流内部共用的小工具（legacy 引擎与 LangGraph 图共用，避免两份实现漂移）。"""

from __future__ import annotations

from ..agents.base import AgentContext


def input_digest(agent_name: str, ctx: AgentContext) -> dict:
    """只存摘要：既便于排查，又不会把简历全文写进库（隐私 + 库膨胀）。"""
    return {
        "agent": agent_name,
        "resume_chars": len(ctx.resume_text),
        "jd_chars": len(ctx.jd_text),
        "work_count": len(ctx.parsed_resume.work),
        "project_count": len(ctx.parsed_resume.projects),
        "bullet_count": len(ctx.parsed_resume.bullets()),
        "skill_count": len(ctx.parsed_resume.skills),
        "jd_skill_count": len(ctx.parsed_jd.all_skills()),
        "knowledge_hits": len(ctx.knowledge_hits),
        "findings_so_far": len(ctx.findings),
        "role_family": ctx.role_family,
    }


def truncate(data: dict | None, limit: int = 4000) -> dict:
    """裁掉过大的输出，避免单行 JSON 过大；保留结构，长文本截断。"""
    if not data:
        return {}

    def cut(value):
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "…[truncated]"
        if isinstance(value, list):
            return [cut(v) for v in value[:30]]
        if isinstance(value, dict):
            return {k: cut(v) for k, v in list(value.items())[:40]}
        return value

    return cut(data)
