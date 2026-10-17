"""Agent 运行时：一次 Agent 运行 = 装配模型/工具 + 有界循环 + 结构化出口 + trace。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from ..schemas import ParsedJd, ParsedResume
from ..services.agent_tools import ToolResult, ToolRunner
from ..services.cost import compute_cost
from ..config import settings
from .models import build_models

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 3000


# --------------------------------------------------------------------------- #
# 声明
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgentBudget:
    """预算四件套。超出即收尾，并在 trace 里标注原因（不静默）。"""

    max_model_calls: int = 8
    max_tool_calls: int = 30
    # 检索轮次：kb_search 的调用次数上限。Agentic RAG 的"多轮"就是靠它约束的。
    max_retrieval_rounds: int = 3
    wall_clock_s: int = 120


@dataclass(frozen=True)
class AgentSpec:
    """一个 Agent 的全部声明。新增 Agent = 写一个这个对象 + system_prompt。"""

    name: str
    title: str
    system_prompt: str
    tool_names: tuple[str, ...]
    finish_model: type[BaseModel]
    finish_description: str
    budget: AgentBudget = AgentBudget()
    finish_name: str = "finish"


@dataclass
class AgentInputs:
    """Agent 的只读输入（一份简历 × 一个 JD，外加可选的任务附加信息）。"""

    resume_text: str
    jd_text: str
    parsed_resume: ParsedResume
    parsed_jd: ParsedJd
    role_family: str = "general"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRun:
    """一次运行的产出 + 可观测轨迹。"""

    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    status: str = "done"          # done | truncated | degraded
    notes: str | None = None
    tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0


# --------------------------------------------------------------------------- #
# 工具装配
# --------------------------------------------------------------------------- #
def build_tools(
    runner: ToolRunner,
    tool_names: tuple[str, ...],
    *,
    finish_model: type[BaseModel],
    finish_name: str = "finish",
    finish_description: str = "提交最终结果并结束。",
) -> list[StructuredTool]:
    """把 `ToolRunner` 的工具按声明裁剪，并追加结构化出口 `finish`。

    工具实现直接转调 `ToolRunner.invoke`，因此**参数夹紧、异常转 ok=False 回灌**
    等行为与旧流水线完全一致（同一份执行器）。
    """
    from ..services import agent_tools as tools_mod

    specs = {name: spec for name, spec in zip(tools_mod.TOOL_NAMES, tools_mod.TOOL_SPECS)}

    out: list[StructuredTool] = []
    for name in tool_names:
        if name not in specs:
            raise ValueError(f"未知工具 {name}；可选 {sorted(specs)}")
        fn_spec = specs[name]["function"]
        args_model = _args_model_of(name)

        async def _call(_runner=runner, _name=name, **kwargs: Any) -> str:
            result = await _runner.invoke(_name, kwargs)
            return json.dumps(result.data, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]

        out.append(
            StructuredTool.from_function(
                coroutine=_call,
                name=name,
                description=fn_spec.get("description") or name,
                args_schema=args_model,
            )
        )

    def _finish(**kwargs: Any) -> str:  # noqa: ARG001 —— 只负责接收，校验在 Agent 之外
        return "已收到，结束。"

    out.append(
        StructuredTool.from_function(
            func=_finish, name=finish_name, description=finish_description, args_schema=finish_model
        )
    )
    return out


def _args_model_of(name: str) -> type[BaseModel]:
    from ..services import agent_tools as t

    mapping = {
        "jd_requirements": t.JdRequirementsArgs,
        "resume_search": t.ResumeSearchArgs,
        "read_section": t.ReadSectionArgs,
        "locate_quote": t.LocateQuoteArgs,
        "kb_search": t.KbSearchArgs,
        "read_chunk": t.ReadChunkArgs,
        "rubric_get": t.RubricGetArgs,
    }
    return mapping[name]


# --------------------------------------------------------------------------- #
# trace
# --------------------------------------------------------------------------- #
def analyze_messages(messages: list[Any], *, finish_name: str = "finish") -> dict[str, Any]:
    """从消息序列提取可观测指标，并把 `finish` 的参数解析出来。"""
    model_calls = 0
    events: list[dict[str, Any]] = []
    executed: list[str] = []
    tokens_in = tokens_out = 0
    cost = 0.0
    finish_args: dict[str, Any] | None = None

    for msg in messages:
        if isinstance(msg, ToolMessage):
            # 只有真正执行过的工具才会有 ToolMessage；被预算拦截的调用没有。
            name = str(getattr(msg, "name", "") or "")
            if name and "Tool call limit reached" not in str(msg.content or ""):
                executed.append(name)
            continue
        if not isinstance(msg, AIMessage):
            continue
        model_calls += 1
        usage = getattr(msg, "usage_metadata", None) or {}
        tin = int(usage.get("input_tokens") or 0)
        tout = int(usage.get("output_tokens") or 0)
        tokens_in += tin
        tokens_out += tout
        cost += compute_cost(
            tin, tout,
            price_in=settings.llm_price_input_per_m,
            price_out=settings.llm_price_output_per_m,
        )
        for call in getattr(msg, "tool_calls", None) or []:
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            events.append({"name": name, "args": args})
            if name == finish_name:
                finish_args = args

    return {
        "model_calls": model_calls,
        "tool_events": events,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": round(cost, 6),
        "finish_args": finish_args,
        "executed_tools": executed,
    }


# --------------------------------------------------------------------------- #
# 运行时
# --------------------------------------------------------------------------- #
class _BudgetedRunner(ToolRunner):
    """带检索轮次上限的工具执行器。

    为什么在**执行器**里限而不是用 `ToolCallLimitMiddleware`：
    实测 `ToolCallLimitMiddleware(tool_name="kb_search", exit_behavior="continue")`
    并没有阻止后续 `kb_search`（同一夹具请求 7 次、执行 7 次，而上限设的是 4）。
    中间件的 per-tool 计数语义不可靠，而"检索轮次"是 Agentic RAG 的核心预算，
    必须确定可控 —— 所以在这里计数：超限时**直接返回错误让模型收尾**，不打库。
    """

    def __init__(self, *, kb_limit: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._kb_limit = kb_limit
        self.kb_requested = 0
        self.kb_executed = 0
        self.kb_blocked = 0
        # 每次实际执行的检索命中了哪些切片（评测"检索质量"与前端观测都要用）
        self.kb_hits: list[dict] = []

    async def invoke(self, name: str, arguments: dict | None) -> ToolResult:  # type: ignore[override]
        if name == "kb_search":
            self.kb_requested += 1
            if self.kb_executed >= self._kb_limit:
                self.kb_blocked += 1
                return ToolResult(
                    ok=False,
                    data={
                        "error": (
                            f"检索轮次已达上限（{self._kb_limit} 轮）。"
                            "请基于已获得的切片收尾：可以直接 read_chunk 复核已有结果，"
                            "或调用 finish 提交当前引用。"
                        )
                    },
                    digest="blocked:kb_search",
                )
            self.kb_executed += 1
        result = await super().invoke(name, arguments)
        if name == "kb_search" and result.ok:
            hits = (result.data or {}).get("hits") or []
            self.kb_hits.append(
                {
                    "query": str((arguments or {}).get("query") or ""),
                    "chunk_ids": [h.get("chunk_id") for h in hits if h.get("chunk_id")],
                }
            )
        return result


class BaseAgent:
    """按 `AgentSpec` 跑一次有界 Agent 循环。"""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    async def run(self, inputs: AgentInputs) -> AgentRun:
        started = time.perf_counter()
        spec = self.spec
        runner = _BudgetedRunner(
            kb_limit=spec.budget.max_retrieval_rounds,
            resume_text=inputs.resume_text,
            jd_text=inputs.jd_text,
            parsed_resume=inputs.parsed_resume,
            parsed_jd=inputs.parsed_jd,
            role_family=inputs.role_family,
        )
        tools = build_tools(
            runner,
            spec.tool_names,
            finish_model=spec.finish_model,
            finish_name=spec.finish_name,
            finish_description=spec.finish_description,
        )
        finish_tool = next(t for t in tools if t.name == spec.finish_name)

        models = build_models(agent=spec.name)
        if not models:
            return AgentRun(
                ok=False, status="degraded", notes="无可用 LLM 供应商（mock 模式）",
                trace={"engine": "agentic", "agent": spec.name, "skipped": "no_llm"},
            )
        primary, *fallbacks = models

        middleware: list[Any] = [
            ModelCallLimitMiddleware(run_limit=spec.budget.max_model_calls, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=spec.budget.max_tool_calls, exit_behavior="end"),
        ]
        if fallbacks:
            middleware.append(ModelFallbackMiddleware(*fallbacks))

        agent = create_agent(
            primary, tools, system_prompt=spec.system_prompt, middleware=middleware, name=spec.name
        )

        recursion_limit = spec.budget.max_model_calls * 4 + 20
        last_state: dict[str, Any] | None = None
        recursion_hit = False
        try:
            async for state in agent.astream(
                {"messages": [HumanMessage(content=self._user_message(inputs))]},
                stream_mode="values",
                config={"recursion_limit": recursion_limit},
            ):
                last_state = state
        except GraphRecursionError:
            recursion_hit = True
            logger.warning("[%s] 触发递归上限 %d，改用已有消息强制收尾", spec.name, recursion_limit)

        messages = list((last_state or {}).get("messages") or [])
        analysis = analyze_messages(messages, finish_name=spec.finish_name)
        truncated = recursion_hit
        forced = False

        if analysis["finish_args"] is None:
            truncated = True
            forced_msg = self._forced_finish(primary, messages, finish_tool)
            if forced_msg is not None:
                forced = True
                for call in getattr(forced_msg, "tool_calls", None) or []:
                    if (call.get("name") or "") == spec.finish_name:
                        analysis["finish_args"] = call.get("args") or {}
                        break
                usage = getattr(forced_msg, "usage_metadata", None) or {}
                analysis["tokens_in"] += int(usage.get("input_tokens") or 0)
                analysis["tokens_out"] += int(usage.get("output_tokens") or 0)
                analysis["model_calls"] += 1

        output = analysis["finish_args"] or {}
        events = analysis["tool_events"]
        trace = {
            "engine": "agentic",
            "agent": spec.name,
            "model_calls": analysis["model_calls"],
            # 工具调用：executed = 真正执行过的（ToolMessage 计数，被拦截的不算）
            "tool_calls": len(analysis["executed_tools"]),
            "tool_names": [e["name"] for e in events],
            "retrieval_requests": runner.kb_requested,
            "retrieval_rounds": runner.kb_executed,
            "retrieval_blocked": runner.kb_blocked,
            "retrieval_queries": [str((e["args"] or {}).get("query") or "") for e in events if e["name"] == "kb_search"],
            # 每次执行的 query + 命中切片（评测用；fixed 链路没有这个字段）
            "retrieval_hits": runner.kb_hits,
            "self_checks": len([n for n in analysis["executed_tools"] if n == "locate_quote"]),
            "truncated": truncated,
            "forced_finish": forced,
            "recursion_hit": recursion_hit,
            "tokens": analysis["tokens_in"] + analysis["tokens_out"],
            "cost": analysis["cost"],
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

        status = "done"
        notes: str | None = None
        if truncated:
            status = "truncated"
            notes = "预算/递归耗尽后强制收尾"
        if not output:
            status = "degraded"
            notes = "未取得结构化输出"

        return AgentRun(
            ok=status == "done",
            output=output,
            trace=trace,
            status=status,
            notes=notes,
            tokens=trace["tokens"],
            cost=trace["cost"],
            latency_ms=trace["latency_ms"],
        )

    # ------------------------------------------------------------------ #
    def _user_message(self, inputs: AgentInputs) -> str:
        parts = [
            f"【JD 标题】{inputs.parsed_jd.title or '（未命名岗位）'}",
            f"【JD 原文】\n{inputs.jd_text}",
            f"【简历原文】\n{inputs.resume_text}",
        ]
        if inputs.extra:
            parts.append("【任务附加信息】\n" + json.dumps(inputs.extra, ensure_ascii=False)[:2000])
        parts.append("请按系统提示完成工作，并在最后调用 finish 提交结构化结果。")
        return "\n\n".join(parts)

    @staticmethod
    def _forced_finish(primary: Any, messages: list[Any], finish_tool: StructuredTool) -> Any:
        try:
            bound = primary.bind_tools(
                [finish_tool],
                tool_choice={"type": "function", "function": {"name": finish_tool.name}},
            )
            return bound.invoke(
                [*messages, HumanMessage(content="材料已足够，请立即调用 finish 提交结果，不要再调用其他工具。")]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("强制收尾失败：%s: %s", type(exc).__name__, str(exc)[:200])
            return None


__all__ = [
    "AgentBudget",
    "AgentInputs",
    "AgentRun",
    "AgentSpec",
    "BaseAgent",
    "build_tools",
    "analyze_messages",
]
