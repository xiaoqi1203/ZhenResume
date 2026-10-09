"""API 路由聚合：统一挂在 /api/v1 下（契约 §0）。"""

from fastapi import APIRouter

from . import admin, billing, feedback, health, hr, jds, knowledge, reports, resumes, tasks, templates

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(templates.router)
api_router.include_router(resumes.router)
api_router.include_router(jds.router)
api_router.include_router(tasks.router)
api_router.include_router(reports.router)
api_router.include_router(billing.plans_router)
api_router.include_router(billing.payments_router)
api_router.include_router(knowledge.router)
api_router.include_router(admin.router)
api_router.include_router(feedback.router)
api_router.include_router(hr.router)

__all__ = ["api_router"]
