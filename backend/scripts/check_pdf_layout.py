"""验证：表格式/双栏 PDF 的版面重建效果（pypdf vs pdfplumber）。

在容器内运行：
    docker cp scripts/check_pdf_layout.py zhenresume-api:/tmp/
    docker exec -e PYTHONPATH=/app zhenresume-api python /tmp/check_pdf_layout.py
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Cm

ROWS = [
    ("姓名", "李四"),
    ("目标岗位", "后端开发工程师"),
    ("联系方式", "13900001111 ｜ lisi@example.com ｜ 上海"),
    ("工作经历", "某支付科技有限公司 | 后端开发工程师 | 2021.03 - 2024.08"),
    ("", "主导支付路由重构，成功率从 99.2% 提升到 99.98%"),
    ("", "搭建对账任务调度平台，日处理 300 万笔"),
    ("教育经历", "某某大学 | 本科 | 软件工程 | 2017.09 - 2021.06"),
    ("专业技能", "Java、Spring Boot、MySQL、Redis、Kafka、分布式事务"),
]


def build_table_docx() -> bytes:
    """生成一份「左标签 / 右内容」表格简历 —— 典型的 ATS 杀手版式。"""
    document = Document()
    for section in document.sections:
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)

    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in ROWS:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        docx_path = tmp_dir / "resume.docx"
        pdf_path = tmp_dir / "resume.pdf"
        docx_path.write_bytes(docx_bytes)
        subprocess.run(
            [
                "/usr/lib/libreoffice/program/soffice",
                f"-env:UserInstallation=file://{tmp_dir}/profile",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(docx_path),
            ],
            capture_output=True,
            timeout=120,
            env={"HOME": str(tmp_dir), "PATH": "/usr/bin:/bin:/usr/lib/libreoffice/program"},
        )
        return pdf_path.read_bytes()


def pypdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def plumber_text(pdf_bytes: bytes) -> str:
    from app.services.extract import _pdf_text_layer_layout

    out = _pdf_text_layer_layout(pdf_bytes)
    return out[0] if out else ""


def main() -> None:
    from app.services.extract import _pdf_text_layer
    from app.services.parse_rules import parse_resume_text

    pdf = docx_to_pdf(build_table_docx())
    print(f"PDF 生成成功：{len(pdf)} 字节，页数检查中…")

    legacy = pypdf_text(pdf)
    modern = plumber_text(pdf)
    chosen, _pages = _pdf_text_layer(pdf)

    print("\n=== 旧实现（pypdf extract_text）前 8 行 ===")
    print("\n".join(legacy.splitlines()[:8]))

    print("\n=== 新实现（pdfplumber 坐标重建）前 8 行 ===")
    print("\n".join(modern.splitlines()[:8]))

    print(f"\n实际采用：{'pdfplumber' if chosen.strip() == modern.strip() else 'pypdf'}")

    parsed = parse_resume_text(chosen)
    name = parsed.basics.name
    title = parsed.basics.target_title
    phone = parsed.basics.phone
    work = parsed.work
    education = parsed.education
    skills = parsed.skills
    print("\n=== 解析结果（新实现）===")
    print(f"姓名={name!r} 岗位={title!r} 手机={phone!r} 城市={parsed.basics.city!r}")
    print(f"工作经历 {len(work)} 段：{[(w.company, w.title, w.start, w.end, len(w.highlights)) for w in work]}")
    print(f"教育 {len(education)} 段：{[(e.school, e.degree, e.major) for e in education]}")
    print(f"技能：{skills}")

    legacy_parsed = parse_resume_text(legacy)
    print("\n=== 对比：旧实现的解析结果 ===")
    print(
        f"姓名={legacy_parsed.basics.name!r} 岗位={legacy_parsed.basics.target_title!r} "
        f"工作经历 {len(legacy_parsed.work)} 段 教育 {len(legacy_parsed.education)} 段 技能 {len(legacy_parsed.skills)} 项"
    )

    ok = bool(work) and bool(education) and bool(skills) and name == "李四"
    print("\n" + ("ALL CHECKS PASSED" if ok else "CHECKS FAILED"))


if __name__ == "__main__":
    main()
