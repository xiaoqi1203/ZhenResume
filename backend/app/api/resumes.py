"""简历接口（契约 §2）：文件上传、编辑器创建、三格式导出。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import FileTooLarge, ResumeNotFound, UnsupportedFileType, ValidationError
from ..models import Resume
from ..schemas import EditorContent, ParsedResume, ResumeFromEditor, ResumeUpdate
from ..serializers import serialize_resume, serialize_resume_summary
from ..services import extract as extract_service
from ..services import dedup as dedup_service
from ..services import resume_render
from ..services.parse_rules import parse_resume_text
from .deps import get_current_user
from ..models import User

router = APIRouter(prefix="/resumes", tags=["resumes"])

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def editor_content_of(resume: Resume) -> EditorContent | None:
    """取出编辑器内容。

    存放位置说明：塞在 `parsed_json["editor_content"]` 里，而不是新加一列。
    原因是 pydantic 默认忽略未知键，所以 `ParsedResume.model_validate` 读到它不会报错；
    这样 MVP 阶段就不必为了一个字段引入数据库迁移。同时这也让
    「这份简历是不是编辑器创建的」可以由 `editor_content` 是否存在来判定。
    """
    raw = resume.parsed_json or {}
    stored = raw.get("editor_content")
    if not isinstance(stored, dict):
        return None
    try:
        return EditorContent.model_validate(stored)
    except Exception:  # noqa: BLE001 —— 内容损坏时退化为解析结果，不让导出整体失败
        return None


def exportable_content(resume: Resume) -> EditorContent:
    """导出用的内容：优先编辑器原文，其次由解析结果重建。"""
    stored = editor_content_of(resume)
    if stored is not None:
        return stored

    try:
        parsed = ParsedResume.model_validate(resume.parsed_json or {})
    except Exception:  # noqa: BLE001
        parsed = ParsedResume.empty()

    content = resume_render.from_parsed_resume(parsed)
    if not (content.work or content.projects or content.education or content.skills or content.summary):
        raise ValidationError(
            "这份简历没有可导出的结构化内容（解析未识别到任何字段）。"
            "请把它载入简历编辑器补全后再导出。"
        )
    return content


def _reused_payload(resume: Resume, note: str) -> dict:
    """去重命中时的响应：完全复用已有简历。

    刻意让响应形状与"新建成功"一致（前端不需要分支判断），只用 `extract_method='dedup'`
    和 `extract_note` 留一条可排查的痕迹 —— 也就是"只去重、不标记"。
    """
    payload = serialize_resume(resume)
    payload["extract_method"] = "dedup"
    payload["extract_note"] = note
    return payload


@router.post("", status_code=201)
async def create_resume(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    filename = file.filename or "resume.txt"
    if not extract_service.is_supported(filename):
        raise UnsupportedFileType()

    data = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise FileTooLarge(f"文件 {len(data) / 1024 / 1024:.1f}MB，超过 {settings.max_upload_mb}MB 上限")
    if not data:
        raise ValidationError("上传的文件为空")

    # ---- 去重（L1 文件级）：命中就复用已有那份，不重复落盘、不重复解析 ----
    # 语义是"只去重、不标记"：不新建行、不在响应里暴露重复信息，用户拿到的是同一份简历。
    file_sha, _ = dedup_service.compute_hashes(None, data)
    hit, _level = dedup_service.find_duplicate(db, user.id, file_sha, None)
    if hit is not None:
        return _reused_payload(hit, "同一文件已上传过，已复用已有简历（未重复创建）")

    # 解析失败要变成"友好的 4xx"，不能让它冒泡成 500。
    # 实测：把一个纯文本文件命名成 .docx 上传，python-docx 会抛 BadZipFile；
    # 用户真实场景里对应的是"文件损坏、被加密、或扩展名与实际格式不符"。
    try:
        result = extract_service.extract_text(data, filename)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(
            f"文件解析失败（可能是文件损坏、被加密，或扩展名与实际格式不符）：{type(exc).__name__}"
        ) from exc
    raw_text = result.text
    parsed = parse_resume_text(raw_text) if raw_text else ParsedResume.empty()

    # ---- 去重（L2 内容级）：换了排版/导出格式但内容一致 ----
    _, content_sha = dedup_service.compute_hashes(raw_text, None)
    hit, _level = dedup_service.find_duplicate(db, user.id, None, content_sha)
    if hit is not None:
        return _reused_payload(hit, "内容与已上传的简历一致，已复用已有简历（未重复创建）")

    # 落盘（生产环境应换 S3 / Supabase Storage，接口形状不变）
    # 放在去重之后：重复上传就不必再占一份磁盘
    suffix = Path(filename).suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = settings.upload_dir / stored_name
    target.write_bytes(data)

    resume = Resume(
        user_id=user.id,
        title=title or filename,
        file_url=f"/static/uploads/{stored_name}",
        raw_text=raw_text,
        parsed_json=parsed.model_dump(),
        file_name=filename,
        mime_type=file.content_type,
        size_bytes=len(data),
        file_sha256=file_sha,
        content_sha256=content_sha or None,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    payload = serialize_resume(resume)
    payload["extract_method"] = result.method
    payload["extract_note"] = result.note
    return payload


@router.post("/from-editor", status_code=201)
def create_resume_from_editor(
    payload: ResumeFromEditor,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """由简历编辑器的结构化内容创建简历（契约 §2.3）。

    不走文件上传：直接把内容渲染成纯文本存进 raw_text，
    因此后续的 8-Agent 诊断流水线与上传的简历完全同一条链路，不需要任何特殊分支。
    """
    content = payload.content
    if not content.is_meaningful():
        raise ValidationError("简历内容太少：请至少填写姓名、手机号或邮箱，以及一段经历或技能。")

    raw_text = resume_render.render_plain_text(content)
    parsed = resume_render.to_parsed_resume(content)
    template_code = (payload.template_code or "").strip() or None

    parsed_json = {**parsed.model_dump(), "editor_content": content.model_dump()}
    if template_code:
        # 存在 parsed_json 里而不是加列：理由同 editor_content（pydantic 忽略未知键）
        parsed_json["template_code"] = template_code

    resume = Resume(
        user_id=user.id,
        title=payload.title,
        file_url=None,
        raw_text=raw_text,
        # 解析结果 + 编辑器原文一起存：前者给诊断用，后者给编辑器回填与无损导出用
        parsed_json=parsed_json,
        file_name=None,
        mime_type=None,
        size_bytes=len(raw_text.encode("utf-8")),
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    return serialize_resume(resume)


@router.put("/{resume_id}")
def update_resume(
    resume_id: str,
    payload: ResumeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """局部更新简历（编辑器「保存」走这里，而不是再 `from-editor` 建一份新的）。

    关键约束：
    - 归属校验与 GET/DELETE 完全一致：不存在 / 非本人 → 404 RESUME_NOT_FOUND
      （不区分「不存在」与「无权限」，避免泄露他人简历 id 是否存在）；
    - 给了 `content` 就用与创建完全相同的渲染链路重算 `raw_text` / `parsed_json`，
      诊断流水线看到的始终是最新内容；`editor_content` 原样保留（它是无损回填的源）；
    - `template_code` 只在请求**显式提供**时才改（`model_fields_set` 判定）：
      content-only 的保存必须保留旧的模板选择，显式 null 才清除；
    - `parsed_json` 是 JSON 列，SQLAlchemy 不追踪字典内部修改，必须整体赋新 dict。
    """
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()

    provided = payload.model_fields_set
    old_parsed = dict(resume.parsed_json or {})
    parsed_json = dict(old_parsed)
    parsed_changed = False

    if payload.title is not None:
        resume.title = payload.title

    if payload.content is not None:
        content = payload.content
        if not content.is_meaningful():
            raise ValidationError("简历内容太少：请至少填写姓名、手机号或邮箱，以及一段经历或技能。")

        raw_text = resume_render.render_plain_text(content)
        parsed = resume_render.to_parsed_resume(content)
        # 重建 parsed_json 时先带回旧的 template_code，避免「只改内容」把模板选择弄丢
        parsed_json = {**parsed.model_dump(), "editor_content": content.model_dump()}
        old_template = old_parsed.get("template_code")
        if isinstance(old_template, str) and old_template.strip():
            parsed_json["template_code"] = old_template.strip()

        resume.raw_text = raw_text
        resume.size_bytes = len(raw_text.encode("utf-8"))
        parsed_changed = True

    if "template_code" in provided:
        new_template = payload.template_code
        if new_template is None:
            if "template_code" in parsed_json:
                parsed_json.pop("template_code", None)
                parsed_changed = True
        elif parsed_json.get("template_code") != new_template:
            parsed_json["template_code"] = new_template
            parsed_changed = True

    if parsed_changed:
        resume.parsed_json = parsed_json

    db.commit()
    db.refresh(resume)
    return serialize_resume(resume)


@router.get("/{resume_id}/export/{format}")
def export_resume(
    resume_id: str,
    format: Literal["markdown", "docx", "pdf"],  # noqa: A002 —— 与契约的路径参数名保持一致
    fit: Literal["none", "one"] = "none",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """导出简历（契约 §2.4）：Markdown / DOCX / PDF。

    `fit=one`：仅对 PDF 生效 —— 逐档缩小字号与间距，尽量压到 1 页 A4，
    实际使用的缩放比例与页数通过 `X-Fit-Scale` / `X-Page-Count` 响应头返回。

    注意是同步函数：PDF 转换是阻塞的外部进程调用，FastAPI 会把同步端点放进线程池执行，
    不会阻塞事件循环。改成 async 反而会卡住整个服务。
    """
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()

    content = exportable_content(resume)
    stem = resume_render.guess_extension(resume.title or "简历")

    if format == "markdown":
        body = resume_render.render_markdown(content)
        return _file_response(
            body.encode("utf-8"),
            "text/markdown; charset=utf-8",
            resume_render.export_filename(stem, "md"),
        )

    if format == "docx":
        docx_bytes = resume_render.render_docx(content)
        return _file_response(docx_bytes, DOCX_MIME, resume_render.export_filename(stem, "docx"))

    if fit == "one":
        pdf_bytes, scale, pages = resume_render.render_pdf_one_page(content)
        return _file_response(
            pdf_bytes,
            "application/pdf",
            resume_render.export_filename(stem, "pdf"),
            extra_headers={
                "X-Fit-Scale": f"{scale:.2f}",
                "X-Page-Count": str(pages),
            },
        )

    pdf_bytes = resume_render.docx_to_pdf(resume_render.render_docx(content))
    pages = resume_render.pdf_page_count(pdf_bytes)
    return _file_response(
        pdf_bytes,
        "application/pdf",
        resume_render.export_filename(stem, "pdf"),
        extra_headers={"X-Page-Count": str(pages)},
    )


def _file_response(
    body: bytes, media_type: str, filename: str, extra_headers: dict[str, str] | None = None
) -> Response:
    # 文件名用 RFC 5987 的 filename* 传中文，避免部分浏览器乱码
    from urllib.parse import quote

    disposition = f"attachment; filename=\"resume\"; filename*=UTF-8''{quote(filename)}"
    headers = {"Content-Disposition": disposition}
    if extra_headers:
        headers.update(extra_headers)
    return Response(content=body, media_type=media_type, headers=headers)


@router.get("")
def list_resumes(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    base = select(Resume).where(Resume.user_id == user.id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(Resume.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [serialize_resume_summary(r) for r in rows], "total": int(total)}


@router.get("/{resume_id}")
def get_resume(
    resume_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()
    return serialize_resume(resume)


@router.delete("/{resume_id}", status_code=204, response_class=Response)
def delete_resume(
    resume_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """删除简历及其级联数据（aaa.md §4 隐私要求：用户可删除数据）。"""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise ResumeNotFound()

    if resume.file_url:
        stored = settings.upload_dir / Path(resume.file_url).name
        stored.unlink(missing_ok=True)

    db.delete(resume)  # 级联删除 analysis_tasks / reports / findings
    db.commit()
    return Response(status_code=204)
