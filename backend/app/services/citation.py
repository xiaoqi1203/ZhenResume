"""知识库引用（chunk 级）的校验、挂载与聚合。

为什么单独一层：
Agent 能产出"看起来很合理"的引用，但**引用是否真的支持结论**必须由服务端判断。
这里的校验是确定性的、可断言的，规则与 `services/evidence` 的证据链原则一致：

1. `chunk_id` 必须真实存在；
2. `quote` 必须能在**该切片文本**中逐字定位（容忍换行/多余空白）；
3. 同一 (chunk_id, quote) 只保留一条；
4. 无法校验的引用**直接丢弃**，并把原因计入统计（不静默，也不阻断主链路）。

挂载策略（`attach_to_findings`）：
- 优先按 Agent 显式给出的 `finding_index` 挂载；
- 否则按 `supports` 文本与 finding 的 title/issue 的重合度做确定性匹配（够保守：宁可挂不上，不乱挂）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeDoc

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

# 文本匹配阈值：supports 与 finding 标题/问题至少有 3 个连续字相同才算一条依据
MIN_OVERLAP_CHARS = 3


def _norm(text: str) -> str:
    return _WS_RE.sub("", text or "")


def verify_citations(
    db: Session,
    citations: Iterable[Any] | None,
    *,
    limit: int = 20,
) -> tuple[list[dict], dict]:
    """校验引用，返回 `(通过的引用, 统计)`。

    通过的引用会补上 `doc_title` / `scope` / `chunk_chars`，便于渲染与展示。
    """
    stats = {"total": 0, "verified": 0, "rejected_missing_chunk": 0, "rejected_quote_not_found": 0, "rejected_duplicate": 0}
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for raw in citations or []:
        stats["total"] += 1
        item = raw if isinstance(raw, dict) else getattr(raw, "model_dump", lambda: {})()
        chunk_id = str(item.get("chunk_id") or "").strip()
        quote = str(item.get("quote") or "").strip()
        supports = str(item.get("supports") or "").strip()
        finding_index = item.get("finding_index")
        if not chunk_id or not quote:
            stats["rejected_quote_not_found"] += 1
            continue

        chunk = db.get(KnowledgeChunk, chunk_id)
        if chunk is None:
            stats["rejected_missing_chunk"] += 1
            continue

        text = chunk.chunk_text or ""
        located = quote in text or _norm(quote) in _norm(text)
        if not located:
            stats["rejected_quote_not_found"] += 1
            continue

        key = (chunk_id, quote)
        if key in seen:
            stats["rejected_duplicate"] += 1
            continue
        seen.add(key)

        doc = db.get(KnowledgeDoc, chunk.doc_id) if chunk.doc_id else None
        verified = {
            "chunk_id": chunk_id,
            "doc_id": chunk.doc_id,
            "doc_title": (doc.title if doc else "") or "",
            "scope": (doc.scope if doc else "") or "",
            "quote": quote,
            "supports": supports,
            "verified": True,
        }
        if isinstance(finding_index, int):
            verified["finding_index"] = finding_index
        out.append(verified)
        stats["verified"] += 1
        if len(out) >= limit:
            break

    return out, stats


def _overlaps(supports: str, finding: dict) -> bool:
    """保守的确定性匹配：supports 与 finding 的标题/问题有足够长的公共片段。"""
    hay = f"{finding.get('title') or ''} {finding.get('issue') or ''} {finding.get('original_text') or ''}"
    hay = _norm(hay)
    sup = _norm(supports)
    if not sup or not hay:
        return False
    if sup in hay:
        return True
    # 用 supports 的连续 N 字片段去试探 hay（N=3 足够保守，且避免单字误挂）
    n = MIN_OVERLAP_CHARS
    for i in range(0, max(1, len(sup) - n + 1)):
        if sup[i : i + n] and sup[i : i + n] in hay:
            return True
    return False


def attach_to_findings(findings: list[dict], verified: list[dict]) -> tuple[list[dict], dict]:
    """把已校验的引用挂到 findings 上（原地修改并返回）。"""
    stats = {"attached": 0, "unmatched": 0}
    if not findings or not verified:
        return findings, stats

    for citation in verified:
        idx = citation.get("finding_index")
        target: dict | None = None
        if isinstance(idx, int) and 0 <= idx < len(findings):
            target = findings[idx]
        else:
            for finding in findings:
                if _overlaps(citation.get("supports") or "", finding):
                    target = finding
                    break
        if target is None:
            stats["unmatched"] += 1
            continue
        target.setdefault("citations", []).append(
            {
                "chunk_id": citation["chunk_id"],
                "doc_id": citation.get("doc_id"),
                "doc_title": citation.get("doc_title") or "",
                "quote": citation["quote"],
                "verified": True,
            }
        )
        stats["attached"] += 1
    return findings, stats


def citations_to_refs(verified: list[dict]) -> list[dict]:
    """把引用聚合成报告级 `knowledge_refs`（与既有结构兼容）。"""
    refs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in verified:
        key = (str(c.get("doc_id") or ""), str(c.get("chunk_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "doc_id": c.get("doc_id"),
                "chunk_id": c.get("chunk_id"),
                "title": c.get("doc_title") or "",
                "scope": c.get("scope") or "",
                "score": None,
            }
        )
    return refs


def split_evidence_and_citations(evidence: dict | None) -> tuple[dict, list[dict]]:
    """把落库时混在 `evidence_ref` 里的 citations 拆出来（保持 evidence 原形状稳定）。"""
    ev = dict(evidence or {})
    citations = ev.pop("citations", None) or []
    return ev, citations


__all__ = [
    "verify_citations",
    "attach_to_findings",
    "citations_to_refs",
    "split_evidence_and_citations",
]
