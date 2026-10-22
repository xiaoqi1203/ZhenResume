"""agent 线的分析节点：把「Agent 自主取证」的产出接回图状态。

## 为什么不能直接把 Agent 输出当 findings

模型产出的 JSON **永远不可信**。`coerce_findings`（fixed 线在用）就是为此而写：
- category / severity 只允许白名单值；
- 引文由后端**重新定位**（不信模型给的 offset）；
- 剔除缺字段、空话、重复的条目。

agent 线**复用同一套收敛逻辑**，只多两步 Agent 特有的处理：
1. **`locate_quote` 自检结果不采信**：模型说自己核验过，服务端仍要再核一次 ——
   这跟 `verify_citations` 的原则一致（Agent 说过什么不算数，能定位才算数）；
2. **`false_gaps` 要做交叉校验**：JD 匹配 Agent 说"这项其实是表述差异"，
   必须能在简历原文找到对应片段才认这个说法，否则会放过真缺口。

## 与规则基线的关系（关键设计）

每个节点**先算规则基线**，再让 Agent 做增量。合并时用 `merge_findings(agent_findings, baseline)`：
- 顺序上 Agent 优先 —— 它可能给出更具体、带证据的表述；
- 但基线条目**不会消失**（被去重覆盖的除外），保证「时间线空窗」这类确定性结论不会因模型波动而丢。

froze 这批基线的 `origin` 标 `rule`，Agent 产出标 `llm`：
- **评分只认 `rule`**（`compute_scores` 的既有约束）→ 同一份简历两次诊断分数仍一致；
- 报告展示两者都要 → 用户既看到确定性硬事实，也看到 Agent 的补充。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from langgraph.runtime import Runtime

from ..agentic import AgentInputs, BaseAgent, get_spec
from ..agentic.analyzers import ANALYZER_SPECS
from ..agents.base import AgentContext, AgentOutput
from ..services import analyze
from ..services.citation import verify_citations
from ..services.evidence import locate_quote
from ..db import session_scope
from .common import input_digest, truncate
from .nodes import WorkflowAbort
from .state import (
    GraphContext,
    WorkflowState,
    ctx_from_state,
    delta_findings,
    safe_jd,
    safe_resume,
    state_slice,
)
from .steps import record_step

logger = logging.getLogger(__name__)

# 规则基线生产者：与 fixed 线使用同一批函数，保证"同口径"
_BASELINE_BUILDERS: dict[str, Callable[[AgentContext], list[dict]]] = {
    "ats": lambda ctx: analyze.ats_findings(
        ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text, ctx.jd_text
    ),
    "impact": lambda ctx: analyze.impact_findings(
        ctx.parsed_resume, ctx.role_family, ctx.resume_text
    ),
    "risk": lambda ctx: analyze.risk_findings(ctx.parsed_resume, ctx.resume_text),
    "jd_match": lambda ctx: analyze.gap_findings(
        ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text, ctx.jd_text
    ),
}


def _agent_inputs(node: str, ctx: AgentContext) -> AgentInputs:
    """按节点组装 Agent 的只读输入。

    规则基线放进 `extra`：Agent 要看到"规则层已经发现了什么"，才能做**增量**
    （补充/修正/剔除），而不是从零重算 —— 从零重算既贵又容易漏掉硬事实。
    """
    extra: dict = {
        "agent_role": node,
        # 规则基线：让 Agent 知道确定性层已经抓到哪些问题
        "rule_baseline": [
            {
                "severity": f.get("severity"),
                "category": f.get("category"),
                "title": f.get("title"),
                "issue": (f.get("issue") or "")[:120],
                "quote": ((f.get("evidence") or {}).get("quote") or "")[:80],
            }
            for f in (_BASELINE_BUILDERS[node](ctx) if node in _BASELINE_BUILDERS else [])
        ][:10],
        # ★ 检索驱动分析（W1）：把进图前那次固定检索的结果作为"已知依据"喂进来。
        #
        # 为什么必须喂：`retrieve_node` 每个任务都跑了一次 top_k=5 检索，但在 agentic 模式下
        # 这份结果**从来没进过分析 Agent 的上下文** —— Agent 只能自己再查一遍，等于两套检索
        # 各查各的、彼此不知道。实测后果：3854 条 findings 里 0 条来自分析阶段的检索。
        #
        # 注意边界：这只是**起点**，不是替代。提示词明确要求"需要时自己再 kb_search"，
        # 自主性一点不减；同时因为不用从零查，能省 1 次模型往返。
        "knowledge_seed": [
            {
                "chunk_id": h.get("chunk_id"),
                "title": h.get("title") or "",
                "scope": h.get("scope") or "",
                "text": (h.get("text") or "")[:400],
            }
            for h in (ctx.knowledge_hits or [])[:3]
        ],
    }
    if node == "jd_match":
        missing, covered = analyze.missing_keywords(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
        extra["missing_keywords"] = missing[:15]
        extra["covered_keywords"] = covered[:15]
    if node == "impact":
        bullets = analyze.content_bullets(ctx.parsed_resume)
        # 硬编码字典只作为**兜底**：提示词要求先 kb_search 查岗位量化标准，
        # 查到了就用检索到的指标名；这条 hint 在检索失败/无命中时才被用上。
        extra["metric_hints_fallback"] = analyze.METRIC_HINTS.get(
            ctx.role_family, analyze.METRIC_HINTS["general"]
        )
        extra["bullet_count"] = len(bullets)
    return AgentInputs(
        resume_text=ctx.resume_text,
        jd_text=ctx.jd_text,
        parsed_resume=ctx.parsed_resume,
        parsed_jd=ctx.parsed_jd,
        role_family=ctx.role_family,
        extra=extra,
    )


def _to_findings(
    node: str,
    output: dict,
    ctx: AgentContext,
    *,
    retrieved_chunk_ids: set[str] | None = None,
) -> list[dict]:
    """把 Agent 的结构化产出转成契约 findings，并做**服务端复核**。

    复核项（任一不通过就修正或丢弃，绝不静默放行）：
    1. 引文定位：模型给的 `evidence_quote` 必须在原文能定位，否则标 `unverified`
       并清空引文（避免报告上出现"证据来源：简历原文 + 一句原文里没有的话"）；
    2. 知识库引用：`citation_chunk_id` 必须真实存在、quote 逐字可定位，
       且（传入 `retrieved_chunk_ids` 时）**必须是本次 Agent 真实检索返回过的切片**
       —— 否则模型只要猜中一个 chunk_id 就能挂上引用；
    3. 必填字段：issue / suggestion / rewritten_text 缺一不可。

    ⚠️ 知识库引用**批量校验**：早期实现是在 for 循环里逐条 `session_scope()`，
    N 条结论 = N 次 DB 会话。W1 让每条结论都可能带引用后，这个开销会被放大 N 倍。
    """
    raw = output.get("findings")
    if not isinstance(raw, list):
        return []

    # ---- 第一遍：收敛字段 + 引文定位，同时把待校验的引用收集起来 ----
    staged: list[tuple[dict, str, str]] = []  # (item, chunk_id, citation_quote)
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        title = str(entry.get("title") or "").strip()
        issue = str(entry.get("issue") or "").strip()
        suggestion = str(entry.get("suggestion") or "").strip()
        rewritten = str(entry.get("rewritten_text") or "").strip()
        if not (issue and suggestion and rewritten):
            continue

        severity = str(entry.get("severity") or "yellow").lower()
        if severity not in {"red", "yellow", "green"}:
            severity = "yellow"
        category = str(entry.get("category") or "").lower()
        if category not in {"ats", "impact", "clarity", "risk", "gap"}:
            category = {"ats": "ats", "impact": "impact", "risk": "risk"}.get(node, "gap")

        original = str(entry.get("original_text") or "").strip()
        quote = str(entry.get("evidence_quote") or "").strip()

        # ① 引文定位复核：模型说核验过不算数，服务端再核一次
        span = locate_quote(ctx.resume_text, quote) if quote else None
        if quote and span is None:
            # 定位失败 → 保留结论但清掉引文，避免"假装有证据"
            logger.info("[%s] 引文定位失败，已清空：%r", node, quote[:40])
            quote = ""
        if not original and quote:
            # original_text 允许为空（结构性结论），但有引文时用它回填，前端左右对照才有内容
            original = quote

        evidence: dict = {
            "source": "resume",
            "quote": quote,
            "span": span,
            "field": str(entry.get("evidence_field") or "").strip() or None,
        }
        if quote and span is None:
            evidence["unverified"] = True

        item = {
            "category": category,
            "severity": severity,
            "title": title or issue[:40],
            "original_text": original,
            "issue": issue,
            "suggestion": suggestion,
            "rewritten_text": rewritten,
            "evidence": evidence,
            # origin=llm：报告照常展示，但**不参与评分**（评分只认规则层，见 analyze.compute_scores）
            "origin": "llm",
        }
        staged.append(
            (item, str(entry.get("citation_chunk_id") or "").strip(),
             str(entry.get("citation_quote") or "").strip())
        )

    # ---- 第二遍：一次 DB 会话批量校验全部知识库引用 ----
    pending = [{"chunk_id": cid, "quote": cq} for _i, cid, cq in staged if cid and cq]
    verified_by_key: dict[tuple[str, str], dict] = {}
    if pending:
        with session_scope() as db:
            verified, stats = verify_citations(db, pending, retrieved_chunk_ids=retrieved_chunk_ids)
            verified_by_key = {(v["chunk_id"], v["quote"]): v for v in verified}
        if stats.get("total") != stats.get("verified"):
            logger.info(
                "[%s] 知识库引用 %d/%d 通过校验（未检索到 %d、quote 定位失败 %d、chunk 不存在 %d）",
                node, stats.get("verified", 0), stats.get("total", 0),
                stats.get("rejected_not_retrieved", 0),
                stats.get("rejected_quote_not_found", 0),
                stats.get("rejected_missing_chunk", 0),
            )

    out: list[dict] = []
    for item, chunk_id, cit_quote in staged:
        # ③ 改写句里的数字必须可追溯到简历原文（W1 新增护栏）
        #
        # 为什么必须在服务端做，而不是只靠提示词：W1 让分析 Agent 先检索规范再写改写句，
        # 而知识库切片正文里**带示例数字**（「CPU 从 85% 降至 45%」「P99 从 480ms 降至 120ms」）。
        # 实测（3 份简历）：模型把 KB 示例的 `85%` 抄进了一条简历里根本没有 CPU 数据的改写句。
        # 这类编造比"不写数字"伤害大得多 —— HR 一追问「你这 85% 哪来的」就穿帮。
        # 提示词里已加禁令，但**模型说过什么不算数**，这里再兜一道：
        # 原文里没有的数字一律替换成占位符，而不是丢弃整条结论（结论本身可能是对的）。
        item["rewritten_text"] = _scrub_unsourced_numbers(
            item["rewritten_text"], ctx.resume_text
        )
        hit = verified_by_key.get((chunk_id, cit_quote))
        if hit:
            item["citations"] = [
                {
                    "chunk_id": hit["chunk_id"],
                    "doc_id": hit.get("doc_id"),
                    "doc_title": hit.get("doc_title") or "",
                    "quote": hit["quote"],
                    "verified": True,
                    # ★ 来源标记：分析阶段自带的依据（生成有据），区别于 qc 之后
                    #   knowledge_evidence Agent 补的引用（结论可信）。两者都要保留。
                    "source": "analyzer",
                }
            ]
        out.append(item)

    return out


def _cross_check_false_gaps(node: str, output: dict, ctx: AgentContext) -> set[str]:
    """复核 JD 匹配 Agent 声称的「表述差异」是否成立。

    这是**唯一允许推翻规则层结论**的地方，所以必须交叉验证：
    Agent 说某项能力"其实简历里有，只是写法不同"，那就要能在简历原文里
    找到对应片段。找不到就当它没说 —— 否则真缺口会被放过，用户拿不到应有的提醒。
    """
    false_gaps: set[str] = set()
    for item in output.get("false_gaps") or []:
        kw = str(item).strip()
        if not kw:
            continue
        # Agent 通过 resume_search 找到的片段会体现在 extra 里，但更可靠的判据是：
        # 该关键词的任一同义表述能在简历里命中。这里用保守策略 ——
        # 只要关键词本身在简历任何位置出现（含技能栏/要点），就认可"表述存在"。
        if locate_quote(ctx.resume_text, kw) is not None:
            false_gaps.add(kw)
            continue
        haystack = analyze.resume_haystack(ctx.parsed_resume, ctx.resume_text)
        if kw.lower() in haystack.lower():
            false_gaps.add(kw)
            continue
        logger.info("[%s] false_gap %r 在简历里找不到依据，不采信", node, kw)
    return false_gaps


def make_analyzer_node(
    node: str,
    *,
    parallel_safe: bool = False,
) -> Callable:
    """构造一个「Agent 自主分析」的契约节点。

    节点名逐字等于 `AGENT_PIPELINE[].name`，因此仍然：
    - 写 `workflow_steps`、计入 progress（8 步契约不破）；
    - 前端时间线照常展示。

    与 fixed 线的 `make_agent_node` 的区别只在**内部实现**：
    固定线是 `agent.safe_run()`（一次 LLM 调用），这里是
    `BaseAgent(spec).run()`（自主循环 + 工具调用 + 结构化出口）。
    """
    spec = ANALYZER_SPECS[node]
    title = spec.title

    async def node_fn(state: WorkflowState, runtime: Runtime[GraphContext]) -> dict:
        task_id = state["task_id"]
        ctx = ctx_from_state(state)
        before = list(ctx.findings)

        await record_step(task_id, node, status="running", input_digest=input_digest(node, ctx))

        # ---- 1. 规则基线（确定性锚点，与 fixed 线同口径）----
        baseline_raw = _BASELINE_BUILDERS[node](ctx) if node in _BASELINE_BUILDERS else []
        from ..agents.finding_utils import coerce_findings

        baseline = coerce_findings(baseline_raw, default_category=node if node != "jd_match" else "gap", ctx=ctx)

        # ---- 2. Agent 自主分析 ----
        semaphore = runtime.context.semaphore
        try:
            inputs = _agent_inputs(node, ctx)
            if semaphore is not None:
                async with semaphore:
                    run = await BaseAgent(get_spec(spec.name)).run(inputs)
            else:
                run = await BaseAgent(get_spec(spec.name)).run(inputs)
        except Exception as exc:  # noqa: BLE001 —— 非 critical，降级为纯规则基线
            logger.warning("[%s] Agent 执行失败，降级为规则基线：%s", node, exc)
            await record_step(
                task_id, node, status="degraded", tokens=0, cost=0,
                latency_ms=0, notes=f"Agent 失败，降级为规则基线：{type(exc).__name__}",
                output_digest={"findings": len(baseline)},
            )
            ctx.add_findings(baseline)
            patch = state_slice(node, ctx)
            if parallel_safe:
                patch.pop("findings", None)
                patch["finding_batches"] = [{"node": node, "items": delta_findings(before, ctx.findings)}]
            else:
                patch["findings"] = ctx.findings
            patch["outputs"] = {node: {"findings_count": len(baseline), "degraded": True}}
            patch["steps"] = [{
                "step_name": node, "contract_step": True, "status": "degraded",
                "tokens": 0, "cost": 0.0, "latency_ms": 0, "notes": "Agent 失败，降级为规则基线",
            }]
            return patch

        # ---- 3. 服务端复核 + 合并 ----
        # 本次 Agent 真实检索到的 chunk 集合：用来校验"引用的切片是不是真的查过"。
        # trace.retrieval_hits 结构见 agentic/runtime.py::_BudgetedRunner.kb_hits。
        retrieved_chunk_ids: set[str] = {
            str(cid)
            for hit in (run.trace.get("retrieval_hits") or [])
            for cid in (hit.get("chunk_ids") or [])
            if cid
        }
        agent_findings = _to_findings(
            node, run.output or {}, ctx, retrieved_chunk_ids=retrieved_chunk_ids
        )
        false_gaps: set[str] = set()
        if node == "jd_match":
            false_gaps = _cross_check_false_gaps(node, run.output or {}, ctx)
            baseline = [f for f in baseline if not _mentions_any(f, false_gaps)]
            # 同时要清掉前面节点已经累积的、被判定为表述差异的条目
            ctx.findings = [f for f in ctx.findings if not _mentions_any(f, false_gaps)]

        from ..agents.finding_utils import merge_findings

        merged = merge_findings(agent_findings, baseline)
        ctx.add_findings(merged)

        if node == "jd_match":
            missing, covered = analyze.missing_keywords(ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
            ctx.missing_keywords = [kw for kw in missing if kw not in false_gaps]
            ctx.covered_keywords = covered

        # ---- 4. 记录步骤（tokens/cost 取 Agent 真实消耗）----
        status = "degraded" if run.status != "done" else "done"
        await record_step(
            task_id, node, status=status,
            tokens=run.tokens, cost=run.cost, latency_ms=run.latency_ms,
            notes=run.notes,
            output_digest=truncate({
                "findings": len(merged),
                "agent_findings": len(agent_findings),
                "baseline": len(baseline),
                "false_gaps": sorted(false_gaps),
                # ★ W1 验收指标：多少条结论真的带上了经校验的知识库依据
                "citations": len([f for f in agent_findings if f.get("citations")]),
                "retrieved_chunks": len(retrieved_chunk_ids),
                "trace": {
                    "model_calls": run.trace.get("model_calls"),
                    "tool_calls": run.trace.get("tool_calls"),
                    "tool_names": run.trace.get("tool_names"),
                    "retrieval_rounds": run.trace.get("retrieval_rounds"),
                },
            }),
        )

        patch: dict = state_slice(node, ctx)
        if parallel_safe:
            patch.pop("findings", None)
            patch["finding_batches"] = [{"node": node, "items": delta_findings(before, ctx.findings)}]
        else:
            patch["findings"] = ctx.findings
        patch["outputs"] = {
            node: {
                "findings_count": len(merged),
                "agent_findings": len(agent_findings),
                "baseline_findings": len(baseline),
                "false_gaps": sorted(false_gaps),
                # ★ W1 验收指标（写进报告 extra_json.agent_outputs，可离线统计）
                "citations_count": len([f for f in agent_findings if f.get("citations")]),
                "retrieved_chunks_count": len(retrieved_chunk_ids),
                "summary": str((run.output or {}).get("summary") or ""),
                # 轨迹落进 outputs，报告 extra_json.agent_outputs 里可查
                "trace": run.trace,
            }
        }
        patch["steps"] = [{
            "step_name": node, "contract_step": True, "status": status,
            "tokens": run.tokens, "cost": run.cost, "latency_ms": run.latency_ms, "notes": run.notes,
        }]
        return patch

    node_fn.__name__ = f"analyzer_node_{node}"
    return node_fn


def _mentions_any(finding: dict, keywords: set[str]) -> bool:
    """该发现项是否在讨论被判定为"表述差异"的关键词。"""
    if not keywords:
        return False
    haystack = f"{finding.get('title', '')} {finding.get('issue', '')} {finding.get('original_text', '')}"
    return any(kw and kw in haystack for kw in keywords)


# --------------------------------------------------------------------------- #
# 改写句数字护栏（W1）
# --------------------------------------------------------------------------- #
# 实现已下沉到 `agents/finding_utils.scrub_unsourced_numbers` —— 因为护栏必须盖住
# **所有**产出 finding 的路径（suggestion_agent 走的是 coerce_findings，不经过这里）。
# 这里保留一个薄别名，避免既有调用点与测试失效。
from ..agents.finding_utils import scrub_unsourced_numbers as _scrub_unsourced_numbers  # noqa: E402


__all__ = ["make_analyzer_node", "_BASELINE_BUILDERS", "_to_findings"]
