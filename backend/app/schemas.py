"""请求体校验模型 + 结构化 Schema（对应 docs/api-contract.md §7）。

响应侧不放 pydantic ResponseModel —— 报告结构较深，用 serializers.py 显式构造 dict，
既能保证字段与契约逐字对齐，也避免 ORM 懒加载带来的额外查询。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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


# ---- 账号（/auth）----
class SendSmsCode(BaseModel):
    # 最小长度放宽到 1：格式交给 services.security.is_valid_phone 判，
    # 前端拿到的是 PHONE_INVALID（中文提示），而不是 pydantic 的英文校验串。
    phone: str = Field(min_length=1, max_length=20)
    # login = 手机验证码登录 / register = 创建账号 / reset = 预留（修改密码）
    purpose: Literal["login", "register", "reset"] = "login"


class LoginEmail(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class LoginSms(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class Register(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    phone: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=4, max_length=8)
    # 至少 8 位、含字母与数字；详细规则在 services/security.py 统一校验
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str | None = Field(default=None, max_length=128)


class ChangePassword(BaseModel):
    # 自动建号的账号没有旧密码，允许为空
    old_password: str | None = Field(default=None, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


class UpdateProfile(BaseModel):
    email: str | None = Field(default=None, max_length=255)

class TaskCreate(BaseModel):
    resume_id: str
    jd_id: str
    # 单份诊断的分析模式：
    #   fixed   = 固定流水线（快，默认）
    #   agentic = Agent 自主取证（慢，但每条结论的引用更充分）
    mode: Literal["fixed", "agentic"] = "fixed"


class ShareCreate(BaseModel):
    """创建分享链接的参数。

    `include_raw` 默认 False：公开页只给结论（分数 + 问题 + 建议），
    不含简历原文引用与改写句；用户显式勾选后才包含。
    """

    include_raw: bool = False


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
    # 头像：前端压缩后的 data URL（`data:image/jpeg;base64,...`）。
    # 不单独走对象存储是有意的：头像小（≤ 600KB base64），跟着简历内容一起存/导出更简单；
    # 诊断链路（ParsedResume）不需要它，渲染 DOCX/PDF 时才嵌入。
    avatar: str = ""

    @field_validator("avatar")
    @classmethod
    def _limit_avatar(cls, value: str) -> str:
        v = (value or "").strip()
        if len(v) > 1_200_000:  # 约 0.9MB 二进制
            raise ValueError("头像图片过大，请压缩到 600KB 以内")
        return v


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


class ResumeUpdate(BaseModel):
    """`PUT /resumes/{id}` 的请求体：局部更新编辑器简历。

    三个字段都是可选的，但**至少要提供一个**（用 `model_validator` 拦在入口，
    避免前端传空对象时后端白跑一趟、还误报 200）。

    语义细节（前端必须知道）：
    - `title` / `content` 省略或传 null = 不改；`content` 提供时必须 `is_meaningful()`；
    - `template_code` 用 `model_fields_set` 区分「没传」和「传 null」：
      没传 → 保留旧值（content-only 更新不会把模板选择弄丢）；
      显式 `"template_code": null` → 删除已存的模板（回到未选模板的状态）。
    """

    title: str | None = None
    template_code: str | None = None
    content: EditorContent | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (1 <= len(v) <= 255):
            raise ValueError("简历标题长度需在 1–255 个字符之间")
        return v

    @field_validator("template_code")
    @classmethod
    def _strip_template_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "ResumeUpdate":
        # 显式传 template_code（哪怕是 null，表示"清除模板"）也算一次有效请求；
        # title/content 传 null 没有实际含义，不计入。
        changed = self.title is not None or self.content is not None or "template_code" in self.model_fields_set
        if not changed:
            raise ValueError("至少提供 title / template_code / content 中的一个字段")
        return self


# --------------------------------------------------------------------------- #
# 结构化 Schema
# --------------------------------------------------------------------------- #
class Basics(BaseModel):
    name: str = ""
    # 目标岗位：解析 PDF/DOCX 与编辑器导入都依赖它，缺失会导致「岗位名」丢字段
    target_title: str = ""
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
    # JD 里「A 或 B」形式的择一要求（如「熟悉 Java 或 Go」）。
    # 命中其中任意一项即视为满足；全部缺失时只报一条"缺 Java 或 Go"，
    # 绝不能把择一条件当成"两项都要"造成误报。
    skill_groups: list[list[str]] = Field(default_factory=list)

    @classmethod
    def empty(cls) -> "ParsedJd":
        return cls()

    def all_skills(self) -> list[str]:
        seen: list[str] = []
        for s in [*self.skills_core, *self.skills_bonus, *self.keywords]:
            if s and s not in seen:
                seen.append(s)
        return seen
