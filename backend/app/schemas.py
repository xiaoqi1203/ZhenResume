"""请求体校验模型 + 结构化 Schema（对应 docs/api-contract.md §7）。

响应侧不放 pydantic ResponseModel —— 报告结构较深，用 serializers.py 显式构造 dict，
既能保证字段与契约逐字对齐，也避免 ORM 懒加载带来的额外查询。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["red", "yellow", "green"]
FindingCategory = Literal["ats", "impact", "clarity", "risk", "gap"]
KnowledgeScope = Literal["role", "industry", "resume_template", "compliance"]
PayProvider = Literal["stripe", "wechat", "alipay"]


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #
class JdCreate(BaseModel):
    title: str | None = None
    company: str | None = None
    raw_text: str = Field(min_length=20, description="JD 原文，至少 20 字")

    @field_validator("raw_text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 20:
            raise ValueError("JD 原文至少 20 字")
        return v


class TaskCreate(BaseModel):
    resume_id: str
    jd_id: str


class KnowledgeDocCreate(BaseModel):
    scope: KnowledgeScope = "role"
    role_family: str | None = None
    doc_type: str | None = None
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: dict | None = None


class KnowledgeSearch(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    scope: KnowledgeScope | None = None


class OrderCreate(BaseModel):
    plan_code: str
    provider: PayProvider = "stripe"


class PaymentCallback(BaseModel):
    order_no: str
    status: Literal["paid", "failed", "refunded"] = "paid"


class FeedbackCreate(BaseModel):
    report_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class BatchCreate(BaseModel):
    jd_id: str
    resume_ids: list[str] = Field(min_length=1, max_length=100)


class PromptUpsert(BaseModel):
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 简历编辑器内容（契约 §2.5）
#
# 设计要点：这不是 ParsedResume。ParsedResume 是「解析结果」，允许缺失、允许解析失败；
# EditorContent 是「用户亲手写的完整内容」，所有键必须存在，且能无损地渲染回文档。
# 两者之间用 resume_render.to_parsed_resume / from_parsed_resume 双向转换，
# 这样编辑器产出的简历与上传的简历在诊断流水线里完全等价。
# --------------------------------------------------------------------------- #
class EditorBasics(BaseModel):
    name: str = ""
    target_title: str = ""
    phone: str = ""
    email: str = ""
    city: str = ""
    links: list[str] = Field(default_factory=list)


class EditorWorkItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    highlights: list[str] = Field(default_factory=list)


class EditorProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    tech: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class EditorEducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start: str = ""
    end: str = ""


class EditorCustomSection(BaseModel):
    title: str = ""
    items: list[str] = Field(default_factory=list)


class EditorContent(BaseModel):
    basics: EditorBasics = Field(default_factory=EditorBasics)
    summary: str = ""
    work: list[EditorWorkItem] = Field(default_factory=list)
    projects: list[EditorProjectItem] = Field(default_factory=list)
    education: list[EditorEducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    custom_sections: list[EditorCustomSection] = Field(default_factory=list)

    def is_meaningful(self) -> bool:
        """是否填了足够的内容值得入库（防止用户点一下就生成一份空简历）。"""
        basics = self.basics
        has_basics = bool(basics.name.strip() and (basics.phone.strip() or basics.email.strip()))
        has_body = bool(self.summary.strip() or self.work or self.projects or self.education or self.skills)
        return has_basics and has_body


class ResumeFromEditor(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    template_code: str | None = None
    content: EditorContent

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("简历标题不能为空")
        return v


# --------------------------------------------------------------------------- #
# 结构化 Schema
# --------------------------------------------------------------------------- #
class Basics(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    links: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start: str = ""
    end: str = ""
    highlights: list[str] = Field(default_factory=list)


class WorkItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    highlights: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    highlights: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)


class TimelineItem(BaseModel):
    start: str = ""
    end: str = ""
    label: str = ""
    type: str = ""


class ParsedResume(BaseModel):
    basics: Basics = Field(default_factory=Basics)
    education: list[EducationItem] = Field(default_factory=list)
    work: list[WorkItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)

    @classmethod
    def empty(cls) -> "ParsedResume":
        return cls()

    def bullets(self) -> list[tuple[str, str]]:
        """产出 (字段路径, 原文段落)，用于证据链定位与原右对照展示。"""
        out: list[tuple[str, str]] = []
        for i, w in enumerate(self.work):
            for j, h in enumerate(w.highlights):
                out.append((f"work[{i}].highlights[{j}]", h))
        for i, p in enumerate(self.projects):
            for j, h in enumerate(p.highlights):
                out.append((f"projects[{i}].highlights[{j}]", h))
        for i, e in enumerate(self.education):
            for j, h in enumerate(e.highlights):
                out.append((f"education[{i}].highlights[{j}]", h))
        return out

    def timeline_entries(self) -> list[tuple[str, str, str]]:
        """产出 (start, end, label)，优先用 timeline，缺省时从 work/education 拼。"""
        if self.timeline:
            return [(t.start, t.end, t.label or t.type) for t in self.timeline]
        out: list[tuple[str, str, str]] = []
        for w in self.work:
            out.append((w.start, w.end, f"{w.company} {w.title}".strip()))
        for e in self.education:
            out.append((e.start, e.end, f"{e.school} {e.degree}".strip()))
        return out


class ParsedJd(BaseModel):
    title: str = ""
    company: str = ""
    seniority: str = ""
    experience_years: int | None = None
    education: str = ""
    skills_core: list[str] = Field(default_factory=list)
    skills_bonus: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @classmethod
    def empty(cls) -> "ParsedJd":
        return cls()

    def all_skills(self) -> list[str]:
        seen: list[str] = []
        for s in [*self.skills_core, *self.skills_bonus, *self.keywords]:
            if s and s not in seen:
                seen.append(s)
        return seen
