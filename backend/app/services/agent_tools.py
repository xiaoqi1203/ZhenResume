"""语义判定 Agent 的工具层（P1）。

设计原则：
1. **零新业务逻辑**：每个 tool 都只是对既有 `services/*` 能力的 JSON Schema 门面，
   Agent 调 tool 与旧流水线调函数走的是同一份实现，因此两条链路的判断口径一致。
2. **一次定义、两处消费**：`TOOL_SPECS` 既给自研循环用（`services.llm.complete_with_tools`），
   也可直接喂给 LangChain 的 `@tool` / `bind_tools`（字段名已按 OpenAI function calling 规范）。
3. **入参校验用 Pydantic**：模型给的参数不可信，越界值一律夹紧（clamp）而不是抛错——
   抛错会把"参数写错"变成"整步失败"，而 Agent 应当能自己看到错误再重试。
4. **执行器持有只读上下文**：`ToolRunner` 只读简历/JD 原文与解析结果，
   不写任何业务状态（写回由 Agent 负责）。

对应文档：`docs/ZhenResume_语义判断接入与Agent封装_改动说明书.md` §5.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ..db import session_scope
from ..models import KnowledgeChunk
from ..schemas import ParsedJd, ParsedResume
from . import analyze, rag
from .evidence import locate_quote

logger = logging.getLogger(__name__)

MAX_SECTION_CHARS = 1200
MAX_HITS = 8
SNIPPET_RADIUS = 40


# --------------------------------------------------------------------------- #
# 入参模型（同时作为 JSON Schema 来源）
# --------------------------------------------------------------------------- #
class JdRequirementsArgs(BaseModel):
    detail: str = Field(default="all", description="core | bonus | keywords | all")


class ResumeSearchArgs(BaseModel):
    keyword: str = Field(min_length=1, description="要在简历中查找的关键词或短语")
    limit: int = Field(default=3, ge=1, le=MAX_HITS)


class ResumeBatchSearchArgs(BaseModel):
    """批量核验关键词是否出现在简历里。

    为什么需要这个工具（实测结论）：分析 Agent 拿到"缺失关键词清单"后，
    会**为每个词单独调一次 resume_search** —— 实测 ats Agent 因此调了 10 次
    `resume_search`，用掉 12 次模型往返、55 秒，最后还因为预算耗尽被截断。
    而这件事本质上是一次"文本包含"判断，批量做是同一份开销。
    """

    keywords: list[str] = Field(
        min_length=1, max_length=20, description="要一次性核验的关键词列表（≤20 个）"
    )


class ReadSectionArgs(BaseModel):
    section: str = Field(description="work | projects | skills | education | certificates | basics")


class LocateQuoteArgs(BaseModel):
    quote: str = Field(min_length=1, description="要核验的原文引文，须逐字来自简历或 JD")
    source: str = Field(default="resume", description="resume | jd")


class LocateBatchArgs(BaseModel):
    """批量核验引文能否定位。

    与 `resume_search` 同理：分析 Agent 提交前要核验每条结论的引文，
    逐条调 `locate_quote` 实测用了 6-8 次工具调用。批量核验是同一个字符串查找。
    """

    quotes: list[str] = Field(
        min_length=1, max_length=20, description="要一次性核验的原文引文列表（≤20 条）"
    )
    source: str = Field(default="resume", description="resume | jd")


class KbSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="检索问题，由你自拟（不必照抄 JD）")
    top_k: int = Field(default=5, ge=1, le=10)
    scope: str | None = Field(default=None, description="role | industry | resume_template | compliance")
    role_family: str | None = Field(default=None, description="backend/frontend/algorithm/... 岗位族")


class ReadChunkArgs(BaseModel):
    chunk_id: str = Field(min_length=1, description="kb_search 返回的 chunk_id")


class RubricGetArgs(BaseModel):
    pass


# --------------------------------------------------------------------------- #
# JSON Schema（OpenAI function calling 规范）
# --------------------------------------------------------------------------- #
def _schema(model: type[BaseModel], name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


TOOL_SPECS: list[dict] = [
    _schema(JdRequirementsArgs, "jd_requirements", "查看岗位要求清单（核心技能/加分项/关键词/职责）"),
    _schema(ResumeSearchArgs, "resume_search", "在简历全文中查找某个关键词，返回其在原文中的片段与位置"),
    _schema(
        ResumeBatchSearchArgs,
        "resume_batch_search",
        "★批量核验：一次检查多个关键词是否出现在简历中。"
        "核验'缺失关键词清单'时用它，比逐个 resume_search 快得多",
    ),
    _schema(ReadSectionArgs, "read_section", "读取简历某一区块的完整内容（工作/项目/技能/教育）"),
    _schema(LocateQuoteArgs, "locate_quote", "★自检工具：核验一段引文能否在简历或 JD 原文中逐字定位"),
    _schema(
        LocateBatchArgs,
        "locate_batch",
        "★批量自检：一次核验多条引文能否定位。提交多条结论前用它，比逐个 locate_quote 快得多",
    ),
    _schema(KbSearchArgs, "kb_search", "检索岗位知识库，获取能力模型/表达式范例/合规依据"),
    _schema(ReadChunkArgs, "read_chunk", "读取知识库某个切片的全文（摘要不够时下钻）"),
    _schema(RubricGetArgs, "rubric_get", "获取本次判定使用的准则（工作记忆，批次内冻结）"),
]

TOOL_NAMES = [s["function"]["name"] for s in TOOL_SPECS]


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #
@dataclass
class ToolResult:
    ok: bool
    data: Any
    digest: str = ""


class ToolRunner:
    """工具执行器：持有只读上下文，按名字分发。"""

    def __init__(
        self,
        *,
        resume_text: str,
        jd_text: str,
        parsed_resume: ParsedResume,
        parsed_jd: ParsedJd,
        role_family: str = "general",
    ) -> None:
        self.resume_text = resume_text or ""
        self.jd_text = jd_text or ""
        self.parsed_resume = parsed_resume
        self.parsed_jd = parsed_jd
        self.role_family = role_family or "general"

    # ------------------------------------------------------------------ #
    async def invoke(self, name: str, arguments: dict | None) -> ToolResult:
        """执行一个 tool。**任何异常都转成 ok=False 的结果回灌给模型**，
        由模型自己决定重试或换路（agentic 的正确降级方式）。"""
        args = arguments or {}
        try:
            if name == "jd_requirements":
                return self._jd_requirements(JdRequirementsArgs(**args))
            if name == "resume_search":
                return self._resume_search(ResumeSearchArgs(**args))
            if name == "resume_batch_search":
                return self._resume_batch_search(ResumeBatchSearchArgs(**args))
            if name == "read_section":
                return self._read_section(ReadSectionArgs(**args))
            if name == "locate_quote":
                return self._locate_quote(LocateQuoteArgs(**args))
            if name == "locate_batch":
                return self._locate_batch(LocateBatchArgs(**args))
            if name == "kb_search":
                return await self._kb_search_async(KbSearchArgs(**args))
            if name == "read_chunk":
                return self._read_chunk(ReadChunkArgs(**args))
            if name == "rubric_get":
                return self._rubric_get()
            return ToolResult(ok=False, data={"error": f"未知工具：{name}"}, digest=f"unknown:{name}")
        except Exception as exc:  # noqa: BLE001 —— 参数校验失败/IO 异常都回灌给模型
            logger.info("tool %s 执行失败：%s: %s", name, type(exc).__name__, str(exc)[:200])
            return ToolResult(ok=False, data={"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, digest=f"error:{name}")

    # ------------------------------------------------------------------ #
    # 各 tool 实现
    # ------------------------------------------------------------------ #
    def _jd_requirements(self, a: JdRequirementsArgs) -> ToolResult:
        pj = self.parsed_jd
        payload = {
            "title": pj.title,
            "seniority": pj.seniority,
            "experience_years": pj.experience_years,
            "education": pj.education,
        }
        if a.detail in ("core", "all"):
            payload["skills_core"] = pj.skills_core
        if a.detail in ("bonus", "all"):
            payload["skills_bonus"] = pj.skills_bonus
        if a.detail in ("keywords", "all"):
            payload["keywords"] = pj.keywords
        if a.detail == "all":
            payload["responsibilities"] = pj.responsibilities[:8]
        return ToolResult(
            ok=True, data=payload,
            digest=f"core={len(pj.skills_core)} bonus={len(pj.skills_bonus)} kw={len(pj.keywords)}",
        )

    def _resume_search(self, a: ResumeSearchArgs) -> ToolResult:
        """在简历里找关键词：既查原文也查解析字段（技能栏等）。"""
        kw = a.keyword.strip()
        variants: list[str] = []
        if locate_quote(self.resume_text, kw):
            variants.append("raw_text")
        low = kw.lower()
        if any(low == s.lower() for s in self.parsed_resume.skills):
            variants.append("skills")
        if any(low in (h or "").lower() for w in self.parsed_resume.work for h in w.highlights):
            variants.append("work.highlights")
        if any(low in (h or "").lower() for p in self.parsed_resume.projects for h in p.highlights):
            variants.append("projects.highlights")

        snippets: list[dict] = []
        idx = self.resume_text.lower().find(low)
        while idx >= 0 and len(snippets) < a.limit:
            start = max(0, idx - SNIPPET_RADIUS)
            end = min(len(self.resume_text), idx + len(kw) + SNIPPET_RADIUS)
            snippets.append({"span_start": start, "span_end": idx + len(kw), "snippet": self.resume_text[start:end]})
            idx = self.resume_text.lower().find(low, idx + len(kw))
        # 若原文没找到但解析字段里有，回填该字段内容
        if not snippets:
            for w in self.parsed_resume.work:
                for h in w.highlights:
                    if low in (h or "").lower() and len(snippets) < a.limit:
                        snippets.append({"field": "work.highlights", "snippet": h[:200]})
            for p in self.parsed_resume.projects:
                for h in p.highlights:
                    if low in (h or "").lower() and len(snippets) < a.limit:
                        snippets.append({"field": "projects.highlights", "snippet": h[:200]})
        return ToolResult(
            ok=True,
            data={"keyword": kw, "found_in": variants, "snippets": snippets},
            digest=f"found={','.join(variants) or 'none'} n={len(snippets)}",
        )

    def _resume_batch_search(self, a: ResumeBatchSearchArgs) -> ToolResult:
        """批量核验关键词是否出现。

        为什么不复用 `_resume_search` 循环：本方法要返回**统一的判定口径**，
        而 `_resume_search` 关心的是"给出可引用的片段"。这里只需要"有没有"，
        顺带给出第一次出现的位置，让模型能用 `locate_quote` 继续核验。

        ⚠️ 大小写与全半角：简历里 `Java`/`java`、`K8s`/`Ｋ8s` 都应算命中，
        所以统一小写比较；但**返回的 snippet 用原文**，模型才能逐字引用。
        """
        haystack = self.resume_text.lower()
        results: list[dict] = []
        for raw_kw in a.keywords[:20]:
            kw = str(raw_kw or "").strip()
            if not kw:
                continue
            low = kw.lower()
            idx = haystack.find(low)
            hit = idx >= 0
            item: dict = {"keyword": kw, "found": hit}
            if hit:
                start = max(0, idx - SNIPPET_RADIUS)
                end = min(len(self.resume_text), idx + len(kw) + SNIPPET_RADIUS)
                item["span"] = [idx, idx + len(kw)]
                item["snippet"] = self.resume_text[start:end]
            else:
                # 没命中原文时再查解析字段（PDF 抽取可能把词拆到不同行）
                lowered = kw.lower()
                where = []
                if any(lowered in (h or "").lower() for w in self.parsed_resume.work for h in w.highlights):
                    where.append("work.highlights")
                if any(lowered in (h or "").lower() for p in self.parsed_resume.projects for h in p.highlights):
                    where.append("projects.highlights")
                if any(lowered == (s or "").lower() for s in self.parsed_resume.skills):
                    where.append("skills")
                if where:
                    item["found"] = True
                    item["found_in"] = where
            results.append(item)

        found = [r["keyword"] for r in results if r["found"]]
        missing = [r["keyword"] for r in results if not r["found"]]
        return ToolResult(
            ok=True,
            data={
                "checked": len(results),
                "found": found,
                "missing": missing,
                "details": results,
            },
            digest=f"checked={len(results)} hit={len(found)} miss={len(missing)}",
        )

    def _locate_batch(self, a: LocateBatchArgs) -> ToolResult:
        """批量核验引文能否逐字定位（与 `_locate_quote` 同一套 `locate_quote`）。"""
        source = "jd" if a.source == "jd" else "resume"
        text = self.jd_text if source == "jd" else self.resume_text
        results: list[dict] = []
        for raw in a.quotes[:20]:
            quote = str(raw or "").strip()
            if not quote:
                continue
            span = locate_quote(text, quote)
            results.append(
                {
                    "quote": quote,
                    "locatable": span is not None,
                    "span": list(span) if span else None,
                }
            )
        ok_count = len([r for r in results if r["locatable"]])
        return ToolResult(
            ok=True,
            data={
                "source": source,
                "locatable": ok_count,
                "total": len(results),
                # 只把**定位失败**的引文点名列出（模型要修的就是这些）
                "failed": [r["quote"] for r in results if not r["locatable"]],
                "details": results,
            },
            digest=f"{source}: {ok_count}/{len(results)} locatable",
        )

    def _read_section(self, a: ReadSectionArgs) -> ToolResult:
        pr = self.parsed_resume
        section = (a.section or "").strip()
        if section == "work":
            data = [
                {"company": w.company, "title": w.title, "start": w.start, "end": w.end,
                 "location": w.location, "highlights": w.highlights}
                for w in pr.work
            ]
        elif section == "projects":
            data = [
                {"name": p.name, "role": p.role, "start": p.start, "end": p.end,
                 "tech": p.tech, "highlights": p.highlights}
                for p in pr.projects
            ]
        elif section == "skills":
            data = {"skills": pr.skills, "certificates": pr.certificates}
        elif section == "education":
            data = [
                {"school": e.school, "degree": e.degree, "major": e.major, "start": e.start, "end": e.end}
                for e in pr.education
            ]
        elif section == "certificates":
            data = {"certificates": pr.certificates}
        elif section == "basics":
            data = {"city": pr.basics.city, "has_email": bool(pr.basics.email), "has_phone": bool(pr.basics.phone)}
        else:
            return ToolResult(ok=False, data={"error": f"未知区块：{section}（可选 work/projects/skills/education/certificates/basics）"})

        text = str(data)
        return ToolResult(ok=True, data={"section": section, "content": data, "truncated": len(text) > MAX_SECTION_CHARS},
                          digest=f"{section} len={len(text)}")

    def _locate_quote(self, a: LocateQuoteArgs) -> ToolResult:
        """★ 自检工具：与后端最终校验用**同一个函数**，避免口径不一致。"""
        quote = a.quote.strip()
        source = "jd" if a.source == "jd" else "resume"
        text = self.jd_text if source == "jd" else self.resume_text
        span = locate_quote(text, quote)
        return ToolResult(
            ok=True,
            data={"quote": quote, "source": source, "locatable": span is not None, "span": list(span) if span else None},
            digest=f"{source}:{'hit' if span else 'MISS'}",
        )

    async def _kb_search_async(self, a: KbSearchArgs) -> ToolResult:
        """知识库检索（同步 IO 放线程池，避免阻塞事件循环）。

        ## 为什么默认仍是单 query 全局排序（`rag.search`）

        曾尝试让 `kb_search` 默认走 `rag.search_scoped`（每 scope 排序 + RRF 融合），
        想解决"Agent 窄 query 第 1 轮 gold 覆盖只有 55%"的问题。**实测否决**：

            真实链路（eval_rag，10 例，同预算第 1 轮口径）
                单 query 全局排序        55.0%
                scope 融合 RRF           58.3%   ← 仅 +3.3pp
            检索器级（eval_retrieval，10 例，recall@5）
                宽 query 全局排序        89.2%
                宽 query scope 融合      58.3%   ← **−30.8pp，严重劣化**

        代价是延迟 +30%（每次检索多算一遍分组）与 200 行复杂逻辑，换 +3.3pp 不划算，
        而且会把宽 query 路径打坏。`rag.search_scoped` 保留在 services/rag.py
        作为**已评测、暂不启用**的方案，需要广度的场景（合规审查等）可显式调用。

        结论：窄 query 的短板不在"排序方式"，在 **query 本身覆盖面窄** ——
        真正的杠杆是让模型把 query 写宽（提示词已要求"一次 kb_search 覆盖一类规范主题"），
        而不是在检索器里硬凑维度。
        """
        import asyncio

        def _run() -> list[dict]:
            with session_scope() as db:
                return rag.search(
                    db, a.query, top_k=a.top_k, scope=a.scope,
                    role_family=a.role_family or (self.role_family if self.role_family != "general" else None),
                )

        hits = await asyncio.to_thread(_run)
        return ToolResult(
            ok=True,
            data={
                "query": a.query,
                "hits": [
                    {"chunk_id": h.get("chunk_id"), "doc_id": h.get("doc_id"), "title": h.get("title"),
                     "scope": h.get("scope"), "role_family": h.get("role_family"),
                     "score": h.get("score"), "text": (h.get("text") or "")[:600]}
                    for h in hits
                ],
                # 让模型看得见"这次覆盖了哪几类依据"，它才知道还缺什么方向
                "scopes_covered": sorted({h.get("scope") or "" for h in hits if h.get("scope")}),
            },
            digest=f"{len(hits)} hits scopes=" + ",".join(
                sorted({h.get("scope") or "" for h in hits if h.get("scope")})
            ),
        )

    def _read_chunk(self, a: ReadChunkArgs) -> ToolResult:
        with session_scope() as db:
            chunk = db.get(KnowledgeChunk, a.chunk_id)
            if chunk is None:
                return ToolResult(ok=False, data={"error": f"切片不存在：{a.chunk_id}"}, digest="miss")
            text = chunk.chunk_text or ""
            return ToolResult(ok=True, data={"chunk_id": a.chunk_id, "text": text, "chars": len(text)},
                              digest=f"chunk len={len(text)}")

    def _rubric_get(self) -> ToolResult:
        """本次判定准则（工作记忆）。batch 内由同一份 JD 解析结果推出，因此天然一致。"""
        pj = self.parsed_jd
        rubric = {
            "version": "rule-v1",
            "role_family": self.role_family,
            "judge_scope": "仅判定 JD 要求项属于【真差距】还是【表述差异】",
            "evidence_rule": "判为表述差异必须给出简历原文中逐字连续的片段；找不到就判真差距",
            "tiers": {
                "core": {"weight": 1.0, "items": pj.skills_core},
                "bonus": {"weight": 0.6, "items": pj.skills_bonus},
                "keyword": {"weight": 0.3, "items": pj.keywords},
            },
        }
        return ToolResult(ok=True, data=rubric, digest=f"rubric core={len(pj.skills_core)} bonus={len(pj.skills_bonus)}")


__all__ = ["TOOL_SPECS", "TOOL_NAMES", "ToolRunner", "ToolResult"]
