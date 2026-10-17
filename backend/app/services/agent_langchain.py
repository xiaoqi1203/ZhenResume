"""LangChain 封装的语义判定 Agent（P2-c）。

与其他两版判定的关系（**同协议、同工具、同校验**，唯一变量是"自主性"）：

| 版本 | 实现 | 工具轮次 | 终止 |
|---|---|---|---|
| P2-b `eval_sort --engine single/cache` | 提示词直接出 JSON | 0 | 固定 |
| P2-a `semantic_agent.run_semantic_single_pass` | 自研：`complete_with_tools` 1 轮 + 结构化产出 | **固定 1 轮** | 固定 |
| **P2-c 本模块** | **LangChain `create_agent` 图** | **模型自主决定轮次与顺序** | 模型调用 `finish` |

为什么用 LangChain 而不是继续手写循环（相对自研循环的增量）：
1. `create_agent` 提供标准的「模型→工具→回灌」状态图与消息管理，省掉手写循环里
   容易出错的 tool_result 拼装、上下文裁剪与重试；
2. **中间件机制**能直接挂预算（`ModelCallLimitMiddleware` / `ToolCallLimitMiddleware`）
   与模型回退（`ModelFallbackMiddleware`），这两件事手写要么没有、要么与主链路不一致；
3. 工具用 `StructuredTool` + Pydantic schema 描述，模型看到的参数定义与执行端校验**同源**。

刻意**不**用 LangChain 的部分（与既有架构约定保持一致）：
- 不用 `ChatOpenAI.with_structured_output`：DashScope 的 `response_format=json_schema`
  支持不确定，而 **tool calling 已实测可用**（`finish` 工具即结构化出口）；
- 不做 LangSmith tracing：会把简历原文外发，`workflow_steps` 才是本项目的事实表；
- 校验仍然走 `services/semantic.verify_semantic_judgments`——**LangChain 不改变可信边界**。

对应文档：docs/ZhenResume_新链路LangChain-多Agent-AgenticRAG方案.md §4、
docs/P2-b_排序验证夹具与结论.md §5（P2-c 的标靶：把 +0.838 推向 oracle +0.980）
"""

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
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from ..config import LlmProviderSpec, settings
from ..schemas import ParsedJd, ParsedResume
from . import analyze, semantic
from .agent_tools import (
    JdRequirementsArgs,
    KbSearchArgs,
    LocateQuoteArgs,
    ReadChunkArgs,
    ReadSectionArgs,
    ResumeSearchArgs,
    RubricGetArgs,
    ToolRunner,
)
from .cost import compute_cost
from .parse_rules import parse_jd_text, parse_resume_text

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 3000


# --------------------------------------------------------------------------- #
# 结构化出口：finish 工具
# --------------------------------------------------------------------------- #
class JudgmentItem(BaseModel):
    """单条判定。字段与 `semantic.verify_semantic_judgments` 的入参对齐。"""

    keyword: str = Field(description="JD 要求中的缺失关键词")
    verdict: str = Field(description="false_gap（表述差异，能力具备）或 real_gap（真差距）")
    resume_quote: str = Field(
        default="",
        description="判为 false_gap 时必填：简历原文中逐字连续的片段（必须出自工作/项目经历要点，≥10 字）",
    )
    reason: str = Field(default="", description="一句话理由")


class FinishArgs(BaseModel):
    judgments: list[JudgmentItem] = Field(description="对**每一个**字面缺失关键词的最终判定")


AGENT_SYSTEM_PROMPT = """你是简历语义判定 Agent。目标：对 JD 要求中「字面缺失」的关键词，
逐个判断它是【真差距】还是【表述差异（能力其实具备，只是用词不同）】。

工作方式（你可以自主决定调用哪些工具、调几次、什么顺序，但**最多 3 轮工具调用**）：
1. 第 1 轮：用 `read_section` 读 `work` 与 `projects`，了解候选人到底做了什么；
2. 第 2 轮：**批量**核实——把缺失关键词放进同一轮的多个 `resume_search` 里，
   不要一个关键词一轮（实测那样会浪费轮次且更容易陷入循环）；
3. 第 3 轮：对仍有疑问的项用 `kb_search` 查行业基准，并用 `locate_quote` **自检**
   你准备引用的每一段原文；
4. 然后**立即调用 `finish`** 提交全部判定。不要在第 4 轮继续调工具。

⚠️ 反复调用相同工具、或对同一个关键词重复搜索，都不会得到新信息；出现这种情况请直接 `finish`。

铁律（违反会被服务端校验直接作废，不影响你的运行但要避免浪费）：
- 判为表述差异时，`resume_quote` 必须逐字复制简历原文，不得改写/拼接；
- 该片段必须出自**工作经历或项目经历的一条要点**——技能栏里的裸技术名词
  （如 `Docker`、`Spring Cloud`）不算证据，长度不足 10 字的碎片也不算；
- 找不到合格证据就判【真差距】。宁可判真差距，也不要编造证据。"""


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #
@dataclass
class LangChainAgentRun:
    semantic_covered: list[tuple[str, str]] = field(default_factory=list)
    judgments: list[dict] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    verify_stats: dict[str, Any] = field(default_factory=dict)
    status: str = "done"          # done | truncated | degraded
    notes: str | None = None
    tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0


# --------------------------------------------------------------------------- #
# 模型与工具装配
# --------------------------------------------------------------------------- #
def _price_of(spec: LlmProviderSpec) -> tuple[float, float]:
    return spec.price_in, spec.price_out


# ⚠️ 模型装配逻辑统一在 `app/agentic/models.py`，这里只做转出，
# 避免「Agent 循环」与「一次性实验」两处各自维护一套阶梯语义。
from ..agentic.models import build_models  # noqa: E402,F401


def _json_result(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]


def build_tools(runner: ToolRunner) -> list[StructuredTool]:
    """把 `agent_tools.ToolRunner` 的 7 个工具包成 LangChain `StructuredTool`，
    并追加 `finish`（结构化出口）。

    工具实现**直接调 ToolRunner**，因此与自研循环/P2-a 的执行口径完全一致
    （参数夹紧、异常转 ok=False 回灌都在 ToolRunner 里）。
    """

    def make(name: str, args_schema: type[BaseModel], description: str) -> StructuredTool:
        async def _call(**kwargs: Any) -> str:
            result = await runner.invoke(name, kwargs)
            return _json_result(result.data)

        return StructuredTool.from_function(
            coroutine=_call,
            name=name,
            description=description,
            args_schema=args_schema,
        )

    tools = [
        make("jd_requirements", JdRequirementsArgs, "查看岗位要求清单（核心技能/加分项/关键词/职责）"),
        make("resume_search", ResumeSearchArgs, "在简历全文中查找关键词，返回原文片段与位置"),
        make("read_section", ReadSectionArgs, "读取简历某区块全文（work/projects/skills/education/certificates/basics）"),
        make("locate_quote", LocateQuoteArgs, "★自检：核验一段引文能否在简历或 JD 原文中逐字定位"),
        make("kb_search", KbSearchArgs, "检索岗位知识库（能力模型/表达范式/合规依据）"),
        make("read_chunk", ReadChunkArgs, "读取知识库某个切片的全文"),
        make("rubric_get", RubricGetArgs, "获取本次判定使用的准则（分层权重与要求清单）"),
    ]

    # finish：只做"接收"，真正的校验在 Agent 之外（semantic.verify_semantic_judgments）
    def _finish(judgments: list[JudgmentItem]) -> str:  # noqa: ARG001
        return f"已记录 {len(judgments)} 条判定，结束。"

    tools.append(
        StructuredTool.from_function(
            func=_finish,
            name="finish",
            description="提交全部判定并结束。必须对每一个缺失关键词都给出一条判定。",
            args_schema=FinishArgs,
        )
    )
    return tools


# --------------------------------------------------------------------------- #
# 消息分析（trace 与成本）
# --------------------------------------------------------------------------- #
def _analyze_messages(messages: list[Any]) -> dict[str, Any]:
    """从最终消息序列里提取可观测指标：模型调用数、工具序列、token、成本、finish 参数。"""
    model_calls = 0
    tool_events: list[dict] = []
    tokens_in = tokens_out = 0
    cost = 0.0
    finish_args: dict | None = None

    for msg in messages:
        if isinstance(msg, AIMessage):
            model_calls += 1
            usage = getattr(msg, "usage_metadata", None) or {}
            tin = int(usage.get("input_tokens") or 0)
            tout = int(usage.get("output_tokens") or 0)
            tokens_in += tin
            tokens_out += tout
            provider = (getattr(msg, "response_metadata", None) or {}).get("model_name") or ""
            # 计价用通用单价：本模块不区分供应商单价（成本只用于横向对比的量级参考）
            cost += compute_cost(
                tin, tout,
                price_in=settings.llm_price_input_per_m,
                price_out=settings.llm_price_output_per_m,
            )
            for call in getattr(msg, "tool_calls", None) or []:
                name = call.get("name") or ""
                args = call.get("args") or {}
                tool_events.append({"name": name, "args": args})
                if name == "finish":
                    finish_args = args
            _ = provider
    return {
        "model_calls": model_calls,
        "tool_events": tool_events,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": round(cost, 6),
        "finish_args": finish_args,
    }


def _forced_finish(model: ChatOpenAI, messages: list[Any], finish_tool: StructuredTool) -> AIMessage | None:
    """预算耗尽的兜底：强制模型调用 `finish` 收尾（对齐设计文档 §5.3「强制收尾」）。"""
    try:
        bound = model.bind_tools(
            [finish_tool],
            tool_choice={"type": "function", "function": {"name": "finish"}},
        )
        return bound.invoke(
            [*messages, HumanMessage(content="材料已足够。请立即调用 finish 提交你当前的判定，不要再调用其他工具。")]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("强制收尾失败：%s: %s", type(exc).__name__, str(exc)[:200])
        return None


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
async def run_semantic_agent_langchain(
    *,
    resume_text: str,
    jd_text: str,
    parsed_resume: ParsedResume | None = None,
    parsed_jd: ParsedJd | None = None,
    role_family: str = "general",
    max_model_calls: int | None = None,
    max_tool_calls: int | None = None,
) -> LangChainAgentRun:
    """跑一次 LangChain Agent（多轮工具 + 自主收敛），返回校验后的等价映射与轨迹。"""
    started = time.perf_counter()
    pr = parsed_resume or parse_resume_text(resume_text)
    pj = parsed_jd or parse_jd_text(jd_text)
    missing, covered = analyze.missing_keywords(pr, pj, resume_text)

    trace: dict[str, Any] = {
        "engine": "langchain_agent",
        "framework": "langchain.create_agent",
        "missing_count": len(missing),
        "model_calls": 0,
        "tool_calls": 0,
        "tool_names": [],
        "retrieval_rounds": 0,
        "self_checks": 0,
        "forced_finish": False,
        "truncated": False,
    }
    if not missing:
        return LangChainAgentRun(trace={**trace, "skipped": "no_missing_keywords"}, notes="无缺失关键词")

    runner = ToolRunner(
        resume_text=resume_text, jd_text=jd_text,
        parsed_resume=pr, parsed_jd=pj, role_family=role_family,
    )
    tools = build_tools(runner)
    finish_tool = next(t for t in tools if t.name == "finish")

    models = build_models()
    if not models:
        return LangChainAgentRun(
            status="degraded",
            trace={**trace, "skipped": "no_llm"},
            notes="无可用 LLM 供应商（mock 模式），Agent 不可用",
        )
    primary, *fallbacks = models
    middleware: list[Any] = [
        ModelCallLimitMiddleware(
            run_limit=max_model_calls or settings.semantic_judge_max_steps,
            exit_behavior="end",
        ),
        ToolCallLimitMiddleware(
            run_limit=max_tool_calls or settings.semantic_judge_max_tool_calls,
            exit_behavior="end",
        ),
    ]
    if fallbacks:
        # 注意：主模型已作为 create_agent 的 model 传入，这里只给**回退链**，
        # 不要把 primary 再当第一个 fallback（那会在失败时原地重试主模型）。
        middleware.append(ModelFallbackMiddleware(*fallbacks))

    agent = create_agent(
        primary,
        tools,
        system_prompt=AGENT_SYSTEM_PROMPT,
        middleware=middleware,
        name="semantic_judge",
    )

    user_msg = (
        f"【JD 标题】{pj.title}\n"
        f"【JD 原文】\n{jd_text}\n\n"
        f"【字面缺失清单】{'、'.join(missing)}\n\n"
        f"【简历原文】\n{resume_text}\n\n"
        "请按系统提示的流程核实后，调用 finish 提交全部判定。"
    )
    # ⚠️ 递归上限必须给足：LangGraph 的 superstep 数 ≈ 模型节点 + 工具节点交替，
    # 实测同一夹具在"无中间件/仅工具限制/仅模型限制/两者"下分别是 10/17/18/23 步，
    # 但不同夹具差异较大（批量工具多时会更高）。给 4×模型预算 + 20 的余量；
    # 真正的成本上限由 middleware 的 run_limit 承担，这里是防死循环的兜底。
    recursion_limit = (max_model_calls or settings.semantic_judge_max_steps) * 4 + 20
    last_state: dict[str, Any] | None = None
    recursion_hit = False
    try:
        async for state in agent.astream(
            {"messages": [HumanMessage(content=user_msg)]},
            stream_mode="values",
            config={"recursion_limit": recursion_limit},
        ):
            last_state = state
    except GraphRecursionError:
        # 撞到递归上限时**不丢已有成果**：用最后一次状态继续做强制收尾
        recursion_hit = True
        logger.warning("Agent 触发递归上限 %d，改用已有消息强制收尾", recursion_limit)

    messages = list((last_state or {}).get("messages") or [])
    analysis = _analyze_messages(messages)
    if recursion_hit:
        trace["recursion_hit"] = True
        trace["truncated"] = True

    # 预算耗尽/超限但没调 finish → 强制收尾一次（与设计文档一致，且不静默）
    if analysis["finish_args"] is None:
        trace["truncated"] = True
        forced = _forced_finish(primary, messages, finish_tool)
        if forced is not None:
            trace["forced_finish"] = True
            forced_calls = getattr(forced, "tool_calls", None) or []
            for call in forced_calls:
                if (call.get("name") or "") == "finish":
                    analysis["finish_args"] = call.get("args") or {}
                    break
            usage = getattr(forced, "usage_metadata", None) or {}
            analysis["tokens_in"] += int(usage.get("input_tokens") or 0)
            analysis["tokens_out"] += int(usage.get("output_tokens") or 0)
            analysis["model_calls"] += 1

    judgments: list[dict] = []
    finish_args = analysis.get("finish_args") or {}
    raw_items = finish_args.get("judgments") if isinstance(finish_args, dict) else None
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                judgments.append(item)

    passed, stats = semantic.verify_semantic_judgments(
        resume_text=resume_text,
        parsed_jd=pj,
        missing_keywords=missing,
        covered_keywords=covered,
        judgments=[{**j, "verdict": j.get("verdict") or ""} for j in judgments],
    )

    tool_events = analysis["tool_events"]
    trace.update(
        {
            "model_calls": analysis["model_calls"],
            "tool_calls": len(tool_events),
            "tool_names": [e["name"] for e in tool_events],
            "retrieval_rounds": len([e for e in tool_events if e["name"] == "kb_search"]),
            "self_checks": len([e for e in tool_events if e["name"] == "locate_quote"]),
            "judgments": len(judgments),
            "semantic_covered": len(passed),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "tokens": analysis["tokens_in"] + analysis["tokens_out"],
            "cost": analysis["cost"],
            "verify_stats": stats,
        }
    )

    notes = semantic.semantic_verify_note(stats)
    status = "done"
    if trace.get("truncated"):
        status = "truncated"
        notes = f"{notes}；预算耗尽后强制收尾"
    if not judgments:
        status = "degraded"
        notes = f"{notes}；未取得任何判定"

    return LangChainAgentRun(
        semantic_covered=passed,
        judgments=judgments,
        trace=trace,
        verify_stats=stats,
        status=status,
        notes=notes,
        tokens=trace["tokens"],
        cost=trace["cost"],
        latency_ms=trace["latency_ms"],
    )


__all__ = ["LangChainAgentRun", "run_semantic_agent_langchain", "build_models", "build_tools", "AGENT_SYSTEM_PROMPT"]
