"""语义判定的**单次调用版**引擎（P2-a，对照臂）。

定位：这是"Agent 工具循环"（P2-c，用 LangChain 封装）的对照基线。
两者必须是**同一协议、同一工具、同一校验**，唯一差别是"允许几轮工具调用、是否由模型决定何时停"：

| | 本模块（single-pass） | P2-c（Agent 循环） |
|---|---|---|
| 协议 | 原生 function calling | 同 |
| 工具 | `agent_tools.TOOL_SPECS` | 同 |
| 工具轮次 | **固定 1 轮**（模型要什么就给什么，然后直接下结论） | 模型自己决定轮次与调用顺序 |
| 终止 | 固定（拿到工具结果就收尾） | 由模型输出 `finish` 决定 |
| 自检重试 | 无（引文定位失败即作废） | 可用 `locate_quote` 自检后重试 |
| 校验/算分 | `services/semantic` + `compute_scores_deterministic` | 同（一字不改） |

这样设计的目的是让 P2-c 的增益**可归因**：唯一变量是"循环与自主性"，
而不是协议、工具或校验口径的差异。

流程（两次模型往返）：
    ① `complete_with_tools(tools=TOOL_SPECS, tool_choice="auto")`
       → 模型按需请求工具（通常 1–3 个）
    ② 用 `ToolRunner` 执行这些工具，结果回灌
    ③ `complete_json` 产出 `{"judgments": [...]}`（结构化输出，不进 tool 循环）
    ④ `semantic.verify_semantic_judgments` 校验 → 可进分数的等价映射
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import analyze, semantic
from .agent_tools import TOOL_SPECS, ToolRunner
from .llm import LLMClient, LLMResult
from .parse_rules import parse_jd_text, parse_resume_text
from ..schemas import ParsedJd, ParsedResume

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 1500

SYSTEM_PROMPT = """你是简历语义判定模块。你的唯一职责：对 JD 要求中"字面缺失"的关键词，
判断它是【真差距】还是【表述差异（能力其实具备，只是用词不同）】。

你可以调用工具核实。**务必先核实再下结论**：
- 关键词是否真的不在简历里 → `resume_search`；
- 简历里某段经历到底做了什么 → `read_section`；
- 某个说法与 JD 用词是否属于同一能力 → `kb_search` 查行业基准；
- 你准备引用的原文片段是否真的存在 → `locate_quote` **自检**。

铁律（违反会被服务端直接作废）：
1) 判定为"表述差异"时，`resume_quote` 必须是**简历原文中逐字连续的片段**，不得改写、不得拼接；
2) 该片段必须来自**工作经历或项目经历的一条要点**——技能栏里的裸技术名词（如 `Docker`、`Spring Cloud`）
   不算证据；长度不足 10 字的碎片也不算。找不到合格证据就判为【真差距】；
3) 只输出 JSON。"""

JUDGE_PROMPT = """【JD 标题】{jd_title}
【字面缺失清单】{missing}

【工具结果】
{tool_results}

请基于上述信息输出最终判定：
{{"judgments":[{{"keyword":"<缺失关键词>","verdict":"false_gap|real_gap",
"resume_quote":"<逐字原文片段，real_gap 留空>","reason":"<一句话>"}}]}}

要求：
1) 对**每一个**缺失关键词都给出判定；
2) 判为【表述差异】必须给出满足铁律 1/2 的 `resume_quote`；给不出就判【真差距】；
3) **逐项核对简历里的实际内容**，不要因为"没直接看到某个词"就一律判真差距 ——
   简历用不同措辞表达同一能力的情况很常见（例如"多服务调用链路治理"对应"分布式"）。"""


def _tool_result_blocks(records: list[dict]) -> str:
    blocks: list[str] = []
    for rec in records:
        payload = json.dumps(rec["data"], ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]
        blocks.append(
            f"--- {rec['name']}({json.dumps(rec['args'], ensure_ascii=False)}) "
            f"→ {'OK' if rec['ok'] else 'ERR'} {rec['digest']}\n{payload}"
        )
    return "\n\n".join(blocks)


@dataclass
class SemanticAgentRun:
    """一次语义判定的完整结果（含审计轨迹）。"""

    semantic_covered: list[tuple[str, str]] = field(default_factory=list)
    judgments: list[dict] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    verify_stats: dict[str, Any] = field(default_factory=dict)
    status: str = "done"          # done | degraded
    notes: str | None = None
    llm: LLMResult | None = None

    @property
    def tokens(self) -> int:
        return int((self.llm.tokens_in + self.llm.tokens_out) if self.llm else 0)

    @property
    def cost(self) -> float:
        return float(self.llm.cost) if self.llm else 0.0


async def run_semantic_single_pass(
    *,
    resume_text: str,
    jd_text: str,
    llm: LLMClient,
    parsed_resume: ParsedResume | None = None,
    parsed_jd: ParsedJd | None = None,
    role_family: str = "general",
    max_tools: int = 4,
) -> SemanticAgentRun:
    """跑一次单轮工具 + 一次结构化产出，返回校验后的等价映射与轨迹。"""
    started = time.perf_counter()
    pr = parsed_resume or parse_resume_text(resume_text)
    pj = parsed_jd or parse_jd_text(jd_text)

    missing, covered = analyze.missing_keywords(pr, pj, resume_text)
    trace: dict[str, Any] = {
        "engine": "single_pass",
        "missing_count": len(missing),
        "tool_calls": 0,
        "tool_names": [],
        "latency_ms": 0,
        "protocol": None,
    }
    if not missing:
        return SemanticAgentRun(
            trace={**trace, "skipped": "no_missing_keywords"},
            notes="无字面缺失关键词，跳过语义判定",
        )

    runner = ToolRunner(
        resume_text=resume_text, jd_text=jd_text,
        parsed_resume=pr, parsed_jd=pj, role_family=role_family,
    )

    # ---- ① 让模型按需请求工具（单轮） ----
    # 关键：**必须把简历原文与 JD 原文一并给出**，并强制它读经历区块。
    # 否则模型会只调 rubric/jd_requirements 就下结论（实测把 9 项全判成真差距）——
    # 那不是"推理不行"，而是"根本没看材料"。
    goal = (
        f"【JD 标题】{pj.title}\n"
        f"【JD 原文】\n{jd_text}\n\n"
        f"【字面缺失清单】{'、'.join(missing)}\n\n"
        f"【简历原文】\n{resume_text}\n\n"
        "请先调用工具把判断所需的材料取全——**至少调用 `read_section` 读取 work 与 projects**，"
        "必要时再用 `resume_search` 逐个核实缺失关键词、用 `kb_search` 查行业基准。"
    )
    first = await llm.complete_with_tools(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": goal}],
        tools=TOOL_SPECS,
        tool_choice="auto",
        agent="semantic_single",
    )
    trace["protocol"] = (first.note or "").split("/")[-1] if first.note else None
    trace["requested_tools"] = [str(c.get("name") or "") for c in first.tool_calls]

    tool_records: list[dict] = []
    requested = [str(c.get("name") or "") for c in first.tool_calls]
    # 兜底：模型没读经历区块时，补上（否则对照臂测的是"没看材料"，不是"推理能力"）
    for required in ("read_section",):
        if required not in requested:
            first.tool_calls.append({"name": required, "arguments": {"section": "work"}})
            trace["forced_tools"] = trace.get("forced_tools", 0) + 1

    for call in first.tool_calls[: max(max_tools, len(first.tool_calls))]:
        name = str(call.get("name") or "")
        args = call.get("arguments") or {}
        result = await runner.invoke(name, args)
        trace["tool_calls"] += 1
        trace["tool_names"].append(name)
        tool_records.append(
            {"name": name, "args": args, "ok": result.ok, "digest": result.digest, "data": result.data}
        )

    # ---- ② 结构化产出（不进工具循环） ----
    judge_user = JUDGE_PROMPT.format(
        jd_title=pj.title or "（未命名岗位）",
        missing="、".join(missing),
        tool_results=_tool_result_blocks(tool_records) if tool_records else "（模型未请求任何工具）",
    )
    judged = await llm.complete_json(
        system=SYSTEM_PROMPT,
        user=judge_user,
        mock_factory=lambda: {"judgments": []},
        agent="semantic_single",
    )
    judgments = judged.data.get("judgments") if isinstance(judged.data.get("judgments"), list) else []

    # ---- ③ 服务端校验（与 Agent 版完全同一入口） ----
    passed, stats = semantic.verify_semantic_judgments(
        resume_text=resume_text, parsed_jd=pj,
        missing_keywords=missing, covered_keywords=covered,
        judgments=judgments,
    )

    trace.update(
        {
            "judgments": len(judgments),
            "semantic_covered": len(passed),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "tokens": first.tokens_in + first.tokens_out + judged.tokens_in + judged.tokens_out,
            "cost": round(first.cost + judged.cost, 6),
            "verify_stats": stats,
        }
    )

    notes = semantic.semantic_verify_note(stats)
    status = "done"
    if first.degraded and judged.degraded:
        status = "degraded"
        notes = f"{notes}；两次 LLM 调用均降级：{judged.note}"

    return SemanticAgentRun(
        semantic_covered=passed,
        judgments=judgments,
        trace=trace,
        verify_stats=stats,
        status=status,
        notes=notes,
        llm=judged,
    )


__all__ = ["SemanticAgentRun", "run_semantic_single_pass", "SYSTEM_PROMPT"]
