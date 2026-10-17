"""知识库 RAG：切片、入库、检索。

检索打分 = 0.6 × 向量余弦 + 0.4 × 词面重合度。
纯向量对短查询（如「Kubernetes」）召回不稳，混合打分的工程性价比更高。
MVP 在 Python 侧做暴力检索；换 pgvector 后改为 SQL `ORDER BY embedding <=> :q` 即可。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeDoc
from . import embeddings

logger = logging.getLogger(__name__)

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
    """把文档切片并写入向量，返回切片数。重复入库前会清掉旧切片。

    切片**批量**向量化（provider=http 时一次请求多条），比逐条调用快一个量级。
    """
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc.id).delete(synchronize_session=False)

    chunks = split_chunks(doc.content or "")
    # strict=True：建索引只走主层、不允许回退（回退会让同一索引混入两个模型的向量）
    vectors = embeddings.embed_many(chunks, strict=True)
    served_model = embeddings.last_served_model()
    for i, (text, vector) in enumerate(zip(chunks, vectors)):
        db.add(
            KnowledgeChunk(
                doc_id=doc.id,
                chunk_text=text,
                embedding=vector,
                doc_metadata={
                    "index": i, "title": doc.title, "scope": doc.scope,
                    # 记下这个向量是哪个模型产的：换模型后没重建索引时，
                    # 检索侧靠它判断"查询向量与库内向量不是同一个模型"并降级
                    "embedding_model": served_model,
                    **(doc.doc_metadata or {}),
                },
            )
        )
    db.flush()
    return len(chunks)


def search(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    scope: str | None = None,
    role_family: str | None = None,
    min_score: float = 0.05,
) -> list[dict]:
    """混合检索，返回 [{chunk_id, doc_id, title, scope, role_family, score, text}]，按相关度降序。

    `role_family`（V2.0 新增，默认 None = 行为与改动前逐位相同）：
    按岗位族过滤知识库。该列在 `knowledge_docs` 上**本来就有索引**、`search` 也一直
    返回它，但此前没有任何调用点传过 —— 属于"能力已就绪、从未接线"。
    语义判定 Agent 与后续 agentic 检索会用它把候选集中到目标岗位族。
    """
    stmt = select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
    if scope:
        stmt = stmt.where(KnowledgeDoc.scope == scope)
    if role_family:
        stmt = stmt.where(KnowledgeDoc.role_family == role_family)

    rows = db.execute(stmt).all()
    if not rows:
        return []

    query_vec = embeddings.embed(query)

    # 一致性守卫（两类都会让 cosine 恒为 0 或失去意义）：
    # ① 维度不一致：换了 embedding 模型但没重建索引；
    # ② **模型不一致**：查询向量由 A 模型产生、库内向量由 B 模型产生 ——
    #    维度可能相同，但向量空间不同，余弦相似度没有意义。
    # 两种情况都退化为**纯词面检索**，保证 RAG 不整体失效，并在日志里明确报出来。
    stored_models = {
        (c.doc_metadata or {}).get("embedding_model")
        for c, _d in rows
        if (c.doc_metadata or {}).get("embedding_model")
    }
    served = embeddings.last_served_model()
    vector_usable = True
    for chunk, _doc in rows:
        if chunk.embedding and len(chunk.embedding) != len(query_vec):
            vector_usable = False
            break
    if vector_usable and stored_models and served and served not in stored_models:
        vector_usable = False
        logger.warning(
            "查询向量来自 %s，但库里是 %s —— 不是同一个模型，纯向量比较无效，"
            "本次退化为纯词面检索。换模型后必须重建索引：python -m scripts.seed_knowledge --force",
            served, "、".join(sorted(stored_models)),
        )
    if not vector_usable and not stored_models:
        logger.warning(
            "查询向量 %d 维与库内向量维度不一致，本次退化为纯词面检索。"
            "请检查 EMBEDDING_* 配置与 EMBEDDING_DIM，并重建索引："
            "python -m scripts.seed_knowledge --force",
            len(query_vec),
        )

    hits: list[dict] = []
    for chunk, doc in rows:
        text = chunk.chunk_text or ""
        lex_score = embeddings.lexical_overlap(query, text)
        if vector_usable:
            score = 0.6 * max(0.0, embeddings.cosine(query_vec, chunk.embedding)) + 0.4 * lex_score
        else:
            score = lex_score
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
