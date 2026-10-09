"""知识库 RAG 接口（契约 §6.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import KnowledgeChunk, KnowledgeDoc, User
from ..schemas import KnowledgeDocCreate, KnowledgeSearch
from ..serializers import serialize_knowledge_doc
from ..services import rag
from .deps import get_current_user

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/docs", status_code=201)
def create_doc(
    payload: KnowledgeDocCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    doc = KnowledgeDoc(
        scope=payload.scope,
        role_family=payload.role_family,
        doc_type=payload.doc_type,
        title=payload.title,
        content=payload.content,
        doc_metadata=payload.metadata or {},
    )
    db.add(doc)
    db.flush()

    chunk_count = rag.index_document(db, doc)
    db.commit()
    db.refresh(doc)

    return serialize_knowledge_doc(doc, chunk_count=chunk_count)


@router.get("/docs")
def list_docs(
    scope: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    base = select(KnowledgeDoc)
    if scope:
        base = base.where(KnowledgeDoc.scope == scope)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(KnowledgeDoc.created_at.desc()).limit(limit).offset(offset)).scalars().all()

    counts = {
        doc_id: count
        for doc_id, count in db.execute(
            select(KnowledgeChunk.doc_id, func.count()).where(
                KnowledgeChunk.doc_id.in_([d.id for d in rows])
            ).group_by(KnowledgeChunk.doc_id)
        ).all()
    } if rows else {}

    return {"items": [serialize_knowledge_doc(d, chunk_count=counts.get(d.id, 0)) for d in rows], "total": int(total)}


@router.post("/search")
def search_knowledge(
    payload: KnowledgeSearch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    hits = rag.search(db, payload.query, top_k=payload.top_k, scope=payload.scope)
    return {"items": hits}
