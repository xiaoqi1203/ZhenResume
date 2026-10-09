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


def coerce_findings(raw: object, *, default_category: str, ctx: AgentContext, limit: int = MAX_FINDINGS) -> list[dict]:
    """把任意形状的列表收敛为合规发现项。"""
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
            "evidence": resolve_evidence(ctx, quote, field),
        }

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
