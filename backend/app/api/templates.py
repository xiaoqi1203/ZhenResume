"""简历模板接口（契约 §6.6）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import TemplateNotFound
from ..services import resume_templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates() -> dict:
    return {"items": [resume_templates.summarize(t) for t in resume_templates.TEMPLATES]}


@router.get("/{code}")
def get_template(code: str) -> dict:
    template = resume_templates.get_template(code)
    if template is None:
        raise TemplateNotFound(f"简历模板不存在：{code}")
    return resume_templates.detail(template)
