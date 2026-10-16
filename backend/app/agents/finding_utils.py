"""发现项规范化 —— 把 LLM 的自由输出收敛成契约 §5.1 的严格结构。

这是「模型输出不可信」与「前端契约必须稳定」之间的那道闸门：
- category / severity 只允许白名单值，越界一律回落到安全默认；
- evidence 由后端**重新定位**（不信模型给的 offset），定位不到就打 unverified 标记；
- 剔除缺字段、重复、空话条目，保证报告里每一条都能落地。
"""

from __future__ import annotations

import re

from ..services.evidence import build_evidence, locate_quote
from .base import AgentContext

VALID_SEVERITY = {"red", "yellow", "green"}
VALID_CATEGORY = {"ats", "impact", "clarity", "risk", "gap"}

# 空话检测：只有这类措辞而没有具体动作的建议一律视为无效
EMPTY_TALK_RE = re.compile(r"^(建议)?(优化|完善|提升|加强|改进|注意|丰富)(一下)?(表述|内容|描述|简历)?[。.]?$")

MAX_FINDINGS = 12


def resolve_evidence(ctx: AgentContext, quote: str, field: str | None = None) -> dict:
    """按引文实际出现的位置判定 source，并给出 span。"""
    quote = (quote or "").strip()
    if quote and locate_quote(ctx.resume_text, quote):
        return build_evidence(source="resume", quote=quote, field=field, resume_text=ctx.resume_text)
    if quote and locate_quote(ctx.jd_text, quote):
        return build_evidence(source="jd", quote=quote, field=field, resume_text=ctx.resume_text, jd_text=ctx.jd_text)
    for hit in ctx.knowledge_hits:
        if quote and quote in (hit.get("text") or ""):
            return {"source": "knowledge", "quote": quote, "span": None, "field": field, "doc_id": hit.get("doc_id"), "chunk_id": hit.get("chunk_id")}
    return {"source": "resume", "quote": quote, "span": None, "field": field, "unverified": True}


def _dedupe_key(item: dict) -> str:
    basis = (item.get("original_text") or item.get("title") or "")[:48]
    return f"{item.get('category')}|{basis}"


def _looks_like_keyword_gap(item: dict) -> bool:
    """是否是「JD 关键词缺失」这一类发现项。"""
    title = item.get("title") or ""
    return "关键词" in title and ("缺失" in title or "未覆盖" in title or "缺少" in title)


def _keyword_from_title(title: str, keywords: list[str]) -> str:
    """从标题里找出对应的缺失关键词（用于校正引文）。

    取**最长**匹配，避免用 `Java` 命中 `JavaScript` 这类父子串误判。
    """
    hits = [k for k in (keywords or []) if k and k in title]
    return max(hits, key=len) if hits else ""


def coerce_findings(
    raw: object,
    *,
    default_category: str,
    ctx: AgentContext,
    limit: int = MAX_FINDINGS,
    origin: str = "rule",
) -> list[dict]:
    """把任意形状的列表收敛为合规发现项。

    `origin` 标记来源：`rule` = 规则层/确定性（mock 与降级链产出也算），`llm` = 模型产出。
    **评分只认 rule**（见 `analyze.compute_scores`），否则同一份简历两次诊断分数会不同。
    """
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()

    for entry in raw:
        if not isinstance(entry, dict):
            continue

        category = entry.get("category")
        if category not in VALID_CATEGORY:
            category = default_category

        severity = entry.get("severity")
        if severity not in VALID_SEVERITY:
            severity = "yellow"

        title = str(entry.get("title") or "").strip()[:250]
        issue = str(entry.get("issue") or entry.get("problem") or "").strip()
        suggestion = str(entry.get("suggestion") or entry.get("advice") or "").strip()
        rewritten = str(entry.get("rewritten_text") or entry.get("rewrite") or "").strip()
        original = str(entry.get("original_text") or "").strip()
        field = entry.get("field") if isinstance(entry.get("field"), str) else None

        # 引文优先级：显式 quote > 规则层已构造的 evidence.quote > original_text。
        # 注意不能直接用 original_text 兜底优先 —— 例如「JD 关键词缺失」这类发现项，
        # original_text 是 JD 首行（岗位名），真正该引用的证据是关键词本身。
        existing_evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
        quote = str(
            entry.get("evidence_quote")
            or entry.get("quote")
            or existing_evidence.get("quote")
            or original
            or ""
        ).strip()

        # 三条硬性要求：有问题、有建议、有可复制改写句
        if not issue or not suggestion or not rewritten:
            continue
        if EMPTY_TALK_RE.match(suggestion) and not rewritten:
            continue
        if not title:
            title = issue[:40]

        item = {
            "category": category,
            "severity": severity,
            "title": title,
            "original_text": original,
            "issue": issue,
            "suggestion": suggestion,
            "rewritten_text": rewritten,
            "evidence": None,  # 下面统一构造
            "origin": origin,
        }

        # 模型自己"发明"的关键词缺失条目：引文经常被写成"已覆盖技能列表"
        # （实测：标题是「缺失核心关键词 Java 及 Spring 生态」，引文却是
        # 「Python、Go、MySQL、Redis、Kafka、Docker」）—— 违背"引文必须是关键词本身"
        # 这条回归要求，也违背证据链只信原文的原则。
        # 处理方式：**把引文校正为标题里对应的缺失关键词**（而不是丢掉整条 ——
        # 丢掉会让"存在红色风险"这条验收断言在 live 下失败）；校正不了才丢弃。
        if origin == "llm" and _looks_like_keyword_gap(item):
            fixed = _keyword_from_title(title, ctx.missing_keywords)
            if not fixed:
                continue
            quote = fixed
            item["original_text"] = fixed

        item["evidence"] = resolve_evidence(ctx, quote, field)

        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break

    return out


def merge_findings(*groups: list[dict]) -> list[dict]:
    """跨 Agent 合并并按 (category, original_text) 去重，保留先出现的（严重度更高的先来）。"""
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            key = _dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged[:MAX_FINDINGS]
