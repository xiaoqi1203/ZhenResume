"""文件文本抽取：PDF / DOCX / PNG / JPG / TXT。

OCR 说明（诚实标注能力边界）：
扫描件识别依赖本机 tesseract，MVP 不强制安装。未安装时返回空文本 + 明确 note，
由解析 Agent 走降级分支（而不是让整个任务崩掉），符合 aaa.md §3.6「支持失败重试与降级」。
要启用 OCR：安装 tesseract 与 `pip install pytesseract` 即可，本模块会自动启用。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class ExtractResult:
    text: str
    method: str
    note: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def file_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_supported(filename: str) -> bool:
    return file_extension(filename) in SUPPORTED_EXTENSIONS


def _extract_pdf(data: bytes) -> ExtractResult:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages).strip()
    note = None
    if not text:
        note = "PDF 未抽到文本层，可能是扫描件；请安装 tesseract 启用 OCR，或改为上传 DOCX/文本。"
    return ExtractResult(text=text, method="pypdf", note=note)


def _extract_docx(data: bytes) -> ExtractResult:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(p for p in parts if p is not None).strip()
    return ExtractResult(text=text, method="python-docx")


def _extract_image(data: bytes) -> ExtractResult:
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except ImportError:
        return ExtractResult(
            text="",
            method="ocr-unavailable",
            note="未安装 pytesseract / tesseract，无法识别图片简历。请上传 PDF、DOCX 或直接粘贴文本。",
        )

    try:
        image = Image.open(io.BytesIO(data))
        # 中文识别需要 chi_sim 语言包；缺失时 tesseract 会抛错，交给上层降级
        text = pytesseract.image_to_string(image, lang="chi_sim+eng").strip()
        return ExtractResult(text=text, method="tesseract")
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(
            text="",
            method="ocr-failed",
            note=f"OCR 识别失败：{type(exc).__name__}: {str(exc)[:160]}",
        )


def _extract_text(data: bytes) -> ExtractResult:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return ExtractResult(text=data.decode(encoding).strip(), method=f"text/{encoding}")
        except UnicodeDecodeError:
            continue
    return ExtractResult(text="", method="text", note="文本编码无法识别")


def extract_text(data: bytes, filename: str) -> ExtractResult:
    """按扩展名分派抽取器。不抛异常，失败信息放在 note 里。"""
    ext = file_extension(filename)
    if ext == ".pdf":
        return _extract_pdf(data)
    if ext == ".docx":
        return _extract_docx(data)
    if ext in IMAGE_EXTENSIONS:
        return _extract_image(data)
    if ext in {".txt", ".md"}:
        return _extract_text(data)
    return ExtractResult(text="", method="unsupported", note=f"不支持的文件类型：{ext}")
