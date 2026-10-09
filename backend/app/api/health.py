"""健康检查（契约 §1）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.version,
        "llm_mode": settings.llm_mode,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model if settings.llm_mode == "live" else "mock-rule-engine",
    }
