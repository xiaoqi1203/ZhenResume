"""简历渲染：EditorContent ⇄ ParsedResume ⇄ 纯文本 / Markdown / DOCX / PDF。

这是本次功能的关键所在。四条渲染路径必须保持一致：

1. **纯文本**：存进 `resumes.raw_text`，喂给现有的 8-Agent 诊断流水线。
   因此它的格式必须能被 `services/parse_rules.py` 正确读回 ——
   章节标题要用词表里的标准说法，经历条目的日期必须落在行尾，
   要点必须以 `- ` 开头。否则「编辑器写出来的简历」诊断质量会低于「上传的简历」。
2. **Markdown**：给用户复制粘贴到招聘网站或网申表单。
3. **DOCX**：给用户投递与微调。刻意**不使用表格、文本框、多栏排版**，
   因为那正是 ATS 解析失败的主要原因（见知识库《ATS 关键词匹配的实际工作方式》）。
4. **PDF**：由 DOCX 转出，因此与 DOCX 的版式完全一致，不会出现两份文件长得不一样。

往返约束：`to_parsed_resume` 与 `from_parsed_resume` 互为逆操作（在 ParsedResume 的信息范围内）。
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..config import settings
from ..errors import PdfUnavailable
from ..schemas import EditorContent, ParsedResume

# 纯文本里的章节标题：必须是 parse_rules.SECTION_KEYWORDS 认得的标准说法
H_BASICS = "个人信息"
H_SUMMARY = "个人优势"
H_WORK = "工作经历"
H_PROJECTS = "项目经历"
H_EDUCATION = "教育经历"
H_SKILLS = "专业技能"
H_CERTIFICATES = "证书"
H_AWARDS = "荣誉奖项"
H_OTHERS = "其他信息"

_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_SAFE_STEM = re.compile(r"[^\w\u4e00-\u9fff.\-]+")


# --------------------------------------------------------------------------- #
# EditorContent ⇄ ParsedResume
# --------------------------------------------------------------------------- #
def to_parsed_resume(content: EditorContent) -> ParsedResume:
    """编辑器内容 → 结构化简历（供诊断流水线使用）。

    注意荣誉奖项并入 certificates：ParsedResume 没有独立字段，
    但它们对招聘方是同类信息，合并后仍可被关键词匹配与展示覆盖到。
    """
    basics = content.basics

    education = [
        {
            "school": item.school,
            "degree": item.degree,
            "major": item.major,
            "start": item.start,
            "end": item.end,
            "highlights": [],
        }
        for item in content.education
    ]
    work = [
        {
            "company": item.company,
            "title": item.title,
            "start": item.start,
            "end": item.end,
            "location": item.location,
            "highlights": list(item.highlights),
        }
        for item in content.work
    ]
    projects = [
        {
            "name": item.name,
            "role": item.role,
            "start": item.start,
            "end": item.end,
            "highlights": list(item.highlights),
            "tech": list(item.tech),
        }
        for item in content.projects
    ]

    timeline: list[dict] = []
    for item in content.work:
        if item.start or item.end:
            timeline.append({"start": item.start, "end": item.end, "label": f"{item.company} {item.title}".strip(), "type": "work"})
    for item in content.education:
        if item.start or item.end:
            timeline.append({"start": item.start, "end": item.end, "label": f"{item.school} {item.degree}".strip(), "type": "education"})

    certificates = [*content.certificates, *content.awards]

    return ParsedResume.model_validate(
        {
            "basics": {
                "name": basics.name,
                "target_title": basics.target_title,
                "email": basics.email,
                "phone": basics.phone,
                "city": basics.city,
                "links": list(basics.links),
            },
            "education": education,
            "work": work,
            "projects": projects,
            "skills": list(content.skills),
            "certificates": certificates,
            "timeline": timeline,
        }
    )


def from_parsed_resume(parsed: ParsedResume) -> EditorContent:
    """结构化简历 → 编辑器内容。

    用于「上传的简历也能在编辑器里打开 / 导出」，以及在编辑器返回时把
    简历里的 summary 也带上。注意这里有个信息损失：ParsedResume 没有
    summary / target_title / awards / custom_sections，因此这些字段为空。
    这是解析结果本身的边界，不是渲染的问题。
    """
    return EditorContent.model_validate(
        {
            "basics": {
                "name": parsed.basics.name,
                "target_title": parsed.basics.target_title,
                "phone": parsed.basics.phone,
                "email": parsed.basics.email,
                "city": parsed.basics.city,
                "links": list(parsed.basics.links),
            },
            "summary": "",
            "work": [
                {
                    "company": item.company,
                    "title": item.title,
                    "start": item.start,
                    "end": item.end,
                    "location": item.location,
                    "highlights": list(item.highlights),
                }
                for item in parsed.work
            ],
            "projects": [
                {
                    "name": item.name,
                    "role": item.role,
                    "start": item.start,
                    "end": item.end,
                    "tech": list(item.tech),
                    "highlights": list(item.highlights),
                }
                for item in parsed.projects
            ],
            "education": [
                {
                    "school": item.school,
                    "degree": item.degree,
                    "major": item.major,
                    "start": item.start,
                    "end": item.end,
                }
                for item in parsed.education
            ],
            "skills": list(parsed.skills),
            "certificates": list(parsed.certificates),
            "awards": [],
            "custom_sections": [],
        }
    )


# --------------------------------------------------------------------------- #
# 共用小工具
# --------------------------------------------------------------------------- #
def _contact_parts(content: EditorContent) -> list[str]:
    basics = content.basics
    parts = [basics.target_title, basics.phone, basics.email]
    if basics.city:
        parts.append(f"现居{basics.city}" if not basics.city.startswith("现居") else basics.city)
    parts.extend(basics.links)
    return [p.strip() for p in parts if p and p.strip()]


def _date_range(start: str, end: str) -> str:
    start, end = start.strip(), end.strip()
    if start and end:
        return f"{start} – {end}"
    return start or end


def _entry_title(*parts: str) -> str:
    return " | ".join([p.strip() for p in parts if p and p.strip()])


# --------------------------------------------------------------------------- #
# 1) 纯文本（喂诊断流水线）
# --------------------------------------------------------------------------- #
def render_plain_text(content: EditorContent) -> str:
    lines: list[str] = []

    if content.basics.name.strip():
        lines.append(content.basics.name.strip())
    contact = _contact_parts(content)
    if contact:
        lines.append(" | ".join(contact))
    lines.append("")

    if content.summary.strip():
        lines += [H_SUMMARY, content.summary.strip(), ""]

    if content.work:
        lines.append(H_WORK)
        for item in content.work:
            header = _entry_title(item.company, item.title)
            date = _date_range(item.start, item.end)
            # 日期必须落在行尾：解析器靠「日期在行首或行尾」判定这是新的经历条目
            lines.append(f"{header}    {date}".rstrip() if date else header)
            if item.location.strip():
                lines.append(f"工作地点：{item.location.strip()}")
            lines.extend(f"- {h.strip()}" for h in item.highlights if h.strip())
            lines.append("")

    if content.projects:
        lines.append(H_PROJECTS)
        for item in content.projects:
            header = _entry_title(item.name, item.role)
            date = _date_range(item.start, item.end)
            lines.append(f"{header}    {date}".rstrip() if date else header)
            tech = [t.strip() for t in item.tech if t.strip()]
            if tech:
                lines.append(f"- 技术栈：{'、'.join(tech)}")
            lines.extend(f"- {h.strip()}" for h in item.highlights if h.strip())
            lines.append("")

    if content.education:
        lines.append(H_EDUCATION)
        for item in content.education:
            header = _entry_title(item.school, item.degree, item.major)
            date = _date_range(item.start, item.end)
            lines.append(f"{header}    {date}".rstrip() if date else header)
        lines.append("")

    skills = [s.strip() for s in content.skills if s.strip()]
    if skills:
        lines += [H_SKILLS, "、".join(skills), ""]

    certificates = [c.strip() for c in content.certificates if c.strip()]
    if certificates:
        lines += [H_CERTIFICATES, "、".join(certificates), ""]

    awards = [a.strip() for a in content.awards if a.strip()]
    if awards:
        lines += [H_AWARDS, "、".join(awards), ""]

    # 自定义区块统一挂在「其他信息」下：解析器认得这个标题，
    # 且刻意不映射到 ParsedResume 字段，避免荣誉/开源这类内容被逐条批评量化。
    for section in content.custom_sections:
        items = [i.strip() for i in section.items if i.strip()]
        if not section.title.strip() and not items:
            continue
        lines.append(f"{H_OTHERS} · {section.title.strip()}" if section.title.strip() else H_OTHERS)
        lines.extend(f"- {item}" for item in items)
        lines.append("")

    text = "\n".join(lines)
    # 折叠 3 个以上连续换行，保持 raw_text 干净
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


# --------------------------------------------------------------------------- #
# 2) Markdown
# --------------------------------------------------------------------------- #
def render_markdown(content: EditorContent) -> str:
    out: list[str] = []

    if content.basics.name.strip():
        out.append(f"# {content.basics.name.strip()}")
        out.append("")
    contact = _contact_parts(content)
    if contact:
        out += [" ｜ ".join(contact), ""]

    if content.summary.strip():
        out += [f"## {H_SUMMARY}", "", content.summary.strip(), ""]

    if content.work:
        out += [f"## {H_WORK}", ""]
        for item in content.work:
            out.append(f"### {_entry_title(item.company, item.title)}")
            meta = " ｜ ".join(p for p in [_date_range(item.start, item.end), item.location.strip()] if p)
            if meta:
                out += ["", f"*{meta}*"]
            out.append("")
            out.extend(f"- {h.strip()}" for h in item.highlights if h.strip())
            out.append("")

    if content.projects:
        out += [f"## {H_PROJECTS}", ""]
        for item in content.projects:
            out.append(f"### {_entry_title(item.name, item.role)}")
            date = _date_range(item.start, item.end)
            if date:
                out += ["", f"*{date}*"]
            out.append("")
            tech = [t.strip() for t in item.tech if t.strip()]
            if tech:
                out.append(f"**技术栈**：{'、'.join(tech)}")
                out.append("")
            out.extend(f"- {h.strip()}" for h in item.highlights if h.strip())
            out.append("")

    if content.education:
        out += [f"## {H_EDUCATION}", ""]
        for item in content.education:
            out.append(f"- {_entry_title(item.school, item.degree, item.major)} ｜ {_date_range(item.start, item.end)}".rstrip(" ｜"))
        out.append("")

    def _list_section(title: str, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if v.strip()]
        if not cleaned:
            return
        out.extend([f"## {title}", ""])
        out.extend(f"- {v}" for v in cleaned)
        out.append("")

    _list_section(H_SKILLS, ["、".join(s.strip() for s in content.skills if s.strip())] if content.skills else [])
    _list_section(H_CERTIFICATES, content.certificates)
    _list_section(H_AWARDS, content.awards)

    for section in content.custom_sections:
        items = [i.strip() for i in section.items if i.strip()]
        if not section.title.strip() and not items:
            continue
        out += [f"## {section.title.strip() or H_OTHERS}", ""]
        out.extend(f"- {item}" for item in items)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# 3) DOCX
# --------------------------------------------------------------------------- #
def _style_run(run, size: float, *, bold: bool = False, color: str | None = None, font: str = "微软雅黑") -> None:
    """显式设置中英文字体。

    只设 run.font.name 对中文无效 —— 中文需要额外写 w:eastAsia 属性，
    否则 Word/WPS 会回落到默认宋体，导出效果与预览不一致。
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), font)
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)


def _avatar_stream(avatar: str):
    """把 `data:image/...;base64,...` 解成字节流；任何异常都返回 None（导出不因头像失败）。"""
    if not avatar or not avatar.startswith("data:") or "," not in avatar:
        return None
    header, _, payload = avatar.partition(",")
    if "base64" not in header:
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) > 2_000_000:
        return None
    import io

    return io.BytesIO(raw)


def render_docx(content: EditorContent, *, scale: float = 1.0) -> bytes:
    """生成 DOCX 字节流。

    `scale` 用于「压缩到一页」：按比例缩小字号、行间距与页边距（1.0 = 原始版式）。
    版式刻意保持单栏纯段落 + 列表：没有表格、没有文本框、没有分栏。
    这样导出的简历能被 ATS 正确解析（对照知识库里的 ATS 说明）。
    """
    import io

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    def S(value: float) -> float:
        """按缩放比例换算尺寸。"""
        return round(value * scale, 2)

    document = Document()

    for section in document.sections:
        # 页面尺寸必须显式设成 A4：python-docx 默认是 Letter（612×792pt），
        # 中文简历按 A4 打印时会多出一截空白或被缩放。
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(S(1.8))
        section.bottom_margin = Cm(S(1.8))
        section.left_margin = Cm(S(2.0))
        section.right_margin = Cm(S(2.0))

    normal = document.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = Pt(S(10.5))
    from docx.oxml.ns import qn

    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    def para(text: str = "", *, size: float = 10.5, bold: bool = False, color: str | None = None,
             space_before: float = 0, space_after: float = 3, center: bool = False):
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(S(space_before))
        p.paragraph_format.space_after = Pt(S(space_after))
        p.paragraph_format.line_spacing = 1.15
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if text:
            _style_run(p.add_run(text), S(size), bold=bold, color=color)
        return p

    def bullet(text: str):
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(S(2))
        p.paragraph_format.left_indent = Cm(S(0.5))
        p.paragraph_format.first_line_indent = Cm(S(-0.35))
        _style_run(p.add_run(f"· {text}"), S(10.5))
        return p

    def section_heading(title: str):
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(S(12))
        p.paragraph_format.space_after = Pt(S(5))
        _style_run(p.add_run(title), S(13), bold=True, color="1F2A44")
        # ATS 常用「下边框」识别章节分隔，这里用段落底边线代替图形元素
        from docx.oxml import OxmlElement

        ppr = p._element.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "2")
        bottom.set(qn("w:color"), "C8CEDB")
        borders.append(bottom)
        ppr.append(borders)

    # --- 抬头 ---
    # 头像：独立段落右对齐，不用表格/文本框包裹（那会让 ATS 解析失败）。
    # 解码失败时静默跳过 —— 不能因为一张图让整份简历导不出来。
    avatar_stream = _avatar_stream(content.basics.avatar)
    if avatar_stream is not None:
        try:
            photo_para = document.add_paragraph()
            photo_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            photo_para.paragraph_format.space_after = Pt(S(2))
            photo_para.add_run().add_picture(avatar_stream, width=Cm(S(2.6)))
        except Exception:  # noqa: BLE001
            pass

    if content.basics.name.strip():
        para(content.basics.name.strip(), size=20, bold=True, space_after=4, center=False)
    contact = _contact_parts(content)
    if contact:
        para(" ｜ ".join(contact), size=10, color="555F73", space_after=2)

    if content.summary.strip():
        section_heading(H_SUMMARY)
        para(content.summary.strip())

    if content.work:
        section_heading(H_WORK)
        for item in content.work:
            title = _entry_title(item.company, item.title)
            date = _date_range(item.start, item.end)
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(S(6))
            p.paragraph_format.space_after = Pt(S(1))
            _style_run(p.add_run(title), S(11.5), bold=True)
            if date:
                _style_run(p.add_run(f"    {date}"), S(10), color="555F73")
            if item.location.strip():
                para(item.location.strip(), size=9.5, color="78829A", space_after=2)
            for h in item.highlights:
                if h.strip():
                    bullet(h.strip())

    if content.projects:
        section_heading(H_PROJECTS)
        for item in content.projects:
            title = _entry_title(item.name, item.role)
            date = _date_range(item.start, item.end)
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(S(6))
            p.paragraph_format.space_after = Pt(S(1))
            _style_run(p.add_run(title), S(11.5), bold=True)
            if date:
                _style_run(p.add_run(f"    {date}"), S(10), color="555F73")
            tech = [t.strip() for t in item.tech if t.strip()]
            if tech:
                para(f"技术栈：{'、'.join(tech)}", size=9.5, color="555F73", space_after=2)
            for h in item.highlights:
                if h.strip():
                    bullet(h.strip())

    if content.education:
        section_heading(H_EDUCATION)
        for item in content.education:
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(S(4))
            p.paragraph_format.space_after = Pt(S(1))
            _style_run(p.add_run(_entry_title(item.school, item.degree, item.major)), S(11))
            date = _date_range(item.start, item.end)
            if date:
                _style_run(p.add_run(f"    {date}"), S(10), color="555F73")

    def text_list(title: str, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if v.strip()]
        if not cleaned:
            return
        section_heading(title)
        para("、".join(cleaned))

    text_list(H_SKILLS, content.skills)
    text_list(H_CERTIFICATES, content.certificates)
    text_list(H_AWARDS, content.awards)

    for section in content.custom_sections:
        items = [i.strip() for i in section.items if i.strip()]
        if not section.title.strip() and not items:
            continue
        section_heading(section.title.strip() or H_OTHERS)
        for item in items:
            bullet(item)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    """调用外部 LibreOffice 把 DOCX 转成 PDF。

    路径来自配置而非硬编码，未配置时抛 PdfUnavailable（503），
    由前端引导用户改用 DOCX 或浏览器打印 —— 宁可明确失败，也不要静默产出一个坏文件。

    ⚠️ 这里用的是 LibreOffice 的**标准 CLI**（`--convert-to pdf --outdir`）。
    之前的实现传的是 `convert --input x --output y`，那不是 soffice 的参数格式，
    即使装了 LibreOffice 也会 returncode != 0（静默失败）。
    """
    if not settings.pdf_enabled:
        raise PdfUnavailable()

    with tempfile.TemporaryDirectory(prefix="zhenresume-pdf-") as tmp:
        tmp_dir = Path(tmp)
        docx_path = tmp_dir / "resume.docx"
        pdf_path = tmp_dir / "resume.pdf"
        docx_path.write_bytes(docx_bytes)

        # 独立的 UserInstallation：并发导出时避免共享 profile 的文件锁冲突
        profile = tmp_dir / "lo-profile"
        env = {
            **os.environ,
            "HOME": str(tmp_dir),
            "TMPDIR": str(tmp_dir),
        }
        cli = settings.libreoffice_cli or settings.libreoffice_node
        try:
            proc = subprocess.run(
                [
                    cli,
                    f"-env:UserInstallation=file://{profile}",
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_dir),
                    str(docx_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.pdf_timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfUnavailable(f"PDF 转换超时（>{settings.pdf_timeout_s:.0f} 秒），请改用 DOCX 导出。") from exc
        except OSError as exc:
            raise PdfUnavailable(f"PDF 转换组件无法启动：{exc}") from exc

        if proc.returncode != 0 or not pdf_path.exists():
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            raise PdfUnavailable(f"PDF 转换失败：{detail or '未知错误'}")

        return pdf_path.read_bytes()


# --------------------------------------------------------------------------- #
# 5) 「压缩到一页」导出
# --------------------------------------------------------------------------- #
# 逐档缩小字号/间距/页边距并重新转 PDF，直到内容落进 1 页 A4。
# 为什么用「反复转换」而不是估算高度：DOCX 的真实分页只有排版引擎说了算，
# 估算出来的一页在 Word/LibreOffice 里经常溢出一行；这里以实际页数为准，宁可多转两次。
FIT_SCALES = (1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68)


def pdf_page_count(pdf_bytes: bytes) -> int:
    """PDF 页数（pypdf 已是依赖，不额外引入）。"""
    import io

    from pypdf import PdfReader

    try:
        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:  # noqa: BLE001
        return 0


def render_pdf_one_page(content: EditorContent) -> tuple[bytes, float, int]:
    """尽量把简历压到 1 页 A4。

    返回 `(pdf_bytes, 实际使用的缩放比例, 页数)`。
    即使所有档位都压不进 1 页，也返回最后一档（最小字号）的结果，由前端提示实际页数。
    """
    last_pdf = b""
    last_scale = FIT_SCALES[-1]
    last_pages = 0
    for scale in FIT_SCALES:
        pdf = docx_to_pdf(render_docx(content, scale=scale))
        pages = pdf_page_count(pdf)
        last_pdf, last_scale, last_pages = pdf, scale, pages
        if pages <= 1:
            break
    return last_pdf, last_scale, last_pages


# --------------------------------------------------------------------------- #
# 下载文件名
# --------------------------------------------------------------------------- #
def export_filename(title: str, extension: str) -> str:
    """生成安全的下载文件名，保留中文但去掉路径分隔符与非法字符。"""
    stem = _ILLEGAL_FILENAME.sub(" ", (title or "简历").strip())
    stem = _SAFE_STEM.sub("_", stem).strip("._ ") or "简历"
    return f"{stem[:60]}.{extension}"


def guess_extension(title: str) -> str:
    """去掉标题里可能带的扩展名，避免导出成 `张三.pdf.docx`。"""
    return re.sub(r"\.(pdf|docx|doc|md|txt)$", "", (title or "").strip(), flags=re.IGNORECASE)
