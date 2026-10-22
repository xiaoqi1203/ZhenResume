"""Agent 基类与共享上下文。

8 个 Agent 的契约（对应 aaa.md §3.6 与 §五.3）：
    parse → jd_match → ats → impact → risk → suggestion → report → qc

统一约定：
- 每个 Agent 只做一件事，输入输出都是 JSON（可存 workflow_steps 表，独立可观测）；
- 每个 Agent 自行提供 `mock_factory`，保证无 API Key 时链路完整；
- 非 critical Agent 抛异常不中断整体任务，由引擎标记 degraded 后继续。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..schemas import ParsedJd, ParsedResume
from ..services.llm import LLMClient, LLMResult


@dataclass
class AgentContext:
    """工作流共享状态。Agent 只读输入部分，产出写回自己负责的字段。"""

    task_id: str
    resume_text: str
    jd_text: str
    parsed_resume: ParsedResume
    parsed_jd: ParsedJd
    role_family: str = "general"
    # 本次任务的模式：fixed | agentic。
    # 只有 qc 的定稿需要它（决定要不要出深度分），但它是"任务属性"而非"节点产出"，
    # 所以放在共享上下文里，避免每个节点各传一遍。
    workflow_mode: str = "fixed"
    knowledge_hits: list[dict] = field(default_factory=list)
    prompt_overrides: dict[str, str] = field(default_factory=dict)

    # 逐层累积的产出
    findings: list[dict] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    covered_keywords: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    knowledge_refs: list[dict] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    # 双分制的深度分（仅 agentic 模式产出）：同一套公式，但模型补充的发现项也参与扣分。
    # fixed 模式下保持空 dict，序列化时出参为 null —— 前端据此隐藏"深度分"卡片。
    depth_scores: dict = field(default_factory=dict)
    summary: str = ""
    qc_report: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)

    # report 阶段定稿、qc 之后仍需要沿用的结论（避免下游再算一遍 → 两处结果打架）
    risk_level: str = ""

    def add_findings(self, items: list[dict]) -> None:
        self.findings.extend(items or [])

    def prompt(self, name: str) -> str:
        from ..services import prompts

        return prompts.resolve(name, self.prompt_overrides)

    def knowledge_digest(self, limit: int = 3, max_chars: int = 300) -> str:
        """把检索到的知识库切片压成提示词片段。"""
        if not self.knowledge_hits:
            return "（无可用知识库依据，请仅依据简历与 JD 原文判断）"
        lines: list[str] = []
        for hit in self.knowledge_hits[:limit]:
            lines.append(f"- [{hit.get('title') or '知识库'}] {str(hit.get('text') or '')[:max_chars]}")
        return "\n".join(lines)


@dataclass
class AgentOutput:
    data: dict
    llm: LLMResult | None = None
    status: str = "done"  # done | degraded
    notes: str | None = None

    @property
    def tokens(self) -> int:
        if not self.llm:
            return 0
        return int(self.llm.tokens_in + self.llm.tokens_out)

    @property
    def cost(self) -> float:
        return float(self.llm.cost) if self.llm else 0.0

    @property
    def latency_ms(self) -> int:
        return int(self.llm.latency_ms) if self.llm else 0


class Agent(ABC):
    name: str = "agent"
    title: str = "Agent"
    critical: bool = False

    @abstractmethod
    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput: ...

    async def safe_run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        """统一异常处理：非 critical 的失败降级为空产出，由引擎记录 notes。"""
        try:
            return await self.run(ctx, llm)
        except Exception as exc:  # noqa: BLE001
            if self.critical:
                raise
            return AgentOutput(
                data={},
                status="degraded",
                notes=f"{self.name} 执行失败，已降级：{type(exc).__name__}: {str(exc)[:200]}",
            )
