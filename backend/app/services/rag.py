"""知识库 RAG：切片、入库、检索。

检索打分 = 0.6 × 向量余弦 + 0.4 × 词面重合度。
纯向量对短查询（如「Kubernetes」）召回不稳，混合打分的工程性价比更高。
MVP 在 Python 侧做暴力检索；换 pgvector 后改为 SQL `ORDER BY embedding <=> :q` 即可。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeDoc
from . import embeddings

_CHUNK_TARGET = 420
_CHUNK_OVERLAP = 60


def split_chunks(content: str, *, target: int = _CHUNK_TARGET, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按段落聚合成定长切片，带少量重叠以保留跨段语义。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[#\-*\d])", content or "") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 1 > target:
            chunks.append(buf)
            buf = (buf[-overlap:] + "\n" + para) if overlap else para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def index_document(db: Session, doc: KnowledgeDoc) -> int:
    """把文档切片并写入向量，返回切片数。重复入库前会清掉旧切片。"""
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc.id).delete(synchronize_session=False)

    count = 0
    for i, text in enumerate(split_chunks(doc.content or "")):
        db.add(
            KnowledgeChunk(
                doc_id=doc.id,
                chunk_text=text,
                embedding=embeddings.embed(text),
                doc_metadata={"index": i, "title": doc.title, "scope": doc.scope, **(doc.doc_metadata or {})},
            )
        )
        count += 1
    db.flush()
    return count


def search(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    scope: str | None = None,
    min_score: float = 0.05,
) -> list[dict]:
    """混合检索，返回 [{chunk_id, doc_id, title, scope, score, text}]，按相关度降序。"""
    stmt = select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
    if scope:
        stmt = stmt.where(KnowledgeDoc.scope == scope)

    rows = db.execute(stmt).all()
    if not rows:
        return []

    query_vec = embeddings.embed(query)
    hits: list[dict] = []
    for chunk, doc in rows:
        text = chunk.chunk_text or ""
        vector_score = embeddings.cosine(query_vec, chunk.embedding)
        lex_score = embeddings.lexical_overlap(query, text)
        score = 0.6 * max(0.0, vector_score) + 0.4 * lex_score
        if score < min_score:
            continue
        hits.append(
            {
                "chunk_id": chunk.id,
                "doc_id": doc.id,
                "title": doc.title or "",
                "scope": doc.scope or "",
                "role_family": doc.role_family or "",
                "score": round(min(score, 1.0), 4),
                "text": text,
            }
        )

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


def build_query(parsed_jd: dict | None, parsed_resume: dict | None) -> str:
    """用 JD 的岗位名 + 技能 + 关键词拼检索 query。"""
    jd = parsed_jd or {}
    parts: list[str] = []
    for key in ("title", "seniority"):
        if jd.get(key):
            parts.append(str(jd[key]))
    for key in ("skills_core", "keywords", "responsibilities"):
        values = jd.get(key) or []
        if isinstance(values, list):
            parts.extend(str(v) for v in values[:6])
    skills = (parsed_resume or {}).get("skills") or []
    parts.extend(str(s) for s in skills[:8])
    return " ".join(parts)[:600]
