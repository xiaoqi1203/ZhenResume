"""文件文本抽取：PDF / DOCX / PNG / JPG / TXT。

分层：本模块只做"按文件格式分派 + 文档容器解析"，
真正把一张图片变成文字的活交给 `services/ocr.py`（本机引擎，零外部 API）。

OCR 说明（诚实标注能力边界）：
- 图片简历（PNG/JPG）→ 直接调本机 OCR 引擎（rapidocr 或 tesseract）。
- 扫描版 PDF → 先试文本层，抽不到文本时回退到"页图 → OCR"：
  ① 优先取 PDF 内嵌位图（pypdf 自带，零额外依赖，对扫描件是无损的原始像素）；
  ② 内嵌位图取不到时，若装了 pypdfium2 则整页渲染再 OCR（覆盖矢量排版）。
- 引擎缺失时返回空文本 + 明确 note，由上层走降级分支（不抛异常、不静默），
  符合 aaa.md §3.6「支持失败重试与降级」。启用方式见 `scripts/check_ocr.py`。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr as ocr_service

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class ExtractResult:
    text: str
    method: str
    note: str | None = None
    # 结构化补充信息（页数、引擎、耗时等）。默认空，接口层按需透出，便于排查"scan 为什么是空的"。
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def file_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_supported(filename: str) -> bool:
    return file_extension(filename) in SUPPORTED_EXTENSIONS


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


def _pdf_text_layer(data: bytes) -> tuple[str, int]:
    """返回 (文本层内容, 页数)。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages).strip(), len(reader.pages)


def _pdf_embedded_images(reader, max_pages: int) -> list[bytes]:
    """取 PDF 里内嵌的位图（扫描件的原始像素就是这么存的）。

    pypdf 自带，不需要额外依赖；对"扫描件 = 每页一张大图"这种最常见形态是最优来源
    —— 直接拿原始像素去 OCR，比重新渲染更清晰。
    """
    images: list[bytes] = []
    for page in list(reader.pages)[:max_pages]:
        try:
            for image in page.images:
                blob = image.data
                if blob:
                    images.append(blob)
        except Exception:  # noqa: BLE001 —— 个别图片用了不支持的滤波器，跳过即可
            continue
    return images


def _pdf_rendered_images(data: bytes, max_pages: int, dpi: int) -> list[bytes]:
    """整页渲染成图片（可选能力：装了 pypdfium2 才有）。覆盖"无内嵌位图"的矢量排版。"""
    import pypdfium2 as pdfium  # type: ignore

    pdf = pdfium.PdfDocument(data)
    out: list[bytes] = []
    for index in range(min(len(pdf), max_pages)):
        bitmap = pdf[index].render(scale=dpi / 72.0)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        out.append(buffer.getvalue())
    return out


def _ocr_pdf_pages(data: bytes, page_count: int) -> ExtractResult:
    """扫描版 PDF 的 OCR 回退：内嵌位图优先，其次整页渲染。"""
    from ..config import settings

    if ocr_service.active_backend() is None:
        return ExtractResult(
            text="",
            method="pypdf",
            note=f"PDF 未抽到文本层（疑似扫描件）。{ocr_service.NO_ENGINE_NOTE}",
            detail={"pages": page_count, "ocr_backend": ""},
        )

    max_pages = max(1, settings.ocr_max_pages)
    truncated = page_count > max_pages

    images: list[bytes] = []
    source = ""
    try:
        from pypdf import PdfReader

        images = _pdf_embedded_images(PdfReader(io.BytesIO(data)), max_pages)
        source = "embedded"
    except Exception:  # noqa: BLE001
        images = []

    if not images:
        try:
            images = _pdf_rendered_images(data, max_pages, settings.ocr_render_dpi)
            source = "rendered"
        except ImportError:
            return ExtractResult(
                text="",
                method="pypdf",
                note=(
                    "PDF 未抽到文本层（疑似扫描件），且页面里没有内嵌位图。"
                    "整页渲染需要 pypdfium2：pip install pypdfium2"
                ),
                detail={"pages": page_count, "ocr_backend": ocr_service.active_backend_name()},
            )
        except Exception as exc:  # noqa: BLE001
            return ExtractResult(
                text="",
                method="pypdf",
                note=f"PDF 页渲染失败：{type(exc).__name__}: {str(exc)[:120]}",
                detail={"pages": page_count, "ocr_backend": ocr_service.active_backend_name()},
            )

    chunks: list[str] = []
    notes: list[str] = []
    backend = ""
    elapsed = 0
    for blob in images[:max_pages]:
        result = ocr_service.recognize_bytes(blob)
        backend = backend or result.backend
        elapsed += result.elapsed_ms
        if result.text:
            chunks.append(result.text)
        if result.note and result.note not in notes:
            notes.append(result.note)

    text = "\n".join(chunks).strip()
    if not text:
        notes.append("扫描件的页面 OCR 后仍没有文字。")

    parts = [f"扫描版 PDF：文本层为空，已对 {len(images[:max_pages])} 页做 OCR（{source}）。"]
    if truncated:
        parts.append(f"仅处理前 {max_pages} 页（OCR_MAX_PAGES），原文件共 {page_count} 页。")
    parts.extend(notes)
    return ExtractResult(
        text=text,
        method=f"pypdf+ocr/{backend or 'unknown'}",
        note=" ".join(parts),
        detail={
            "pages": page_count,
            "ocr_pages": len(images[:max_pages]),
            "ocr_source": source,
            "ocr_backend": backend,
            "elapsed_ms": elapsed,
        },
    )


def _extract_pdf(data: bytes) -> ExtractResult:
    from ..config import settings

    text, page_count = _pdf_text_layer(data)
    if len(text) >= settings.ocr_text_layer_min_chars:
        return ExtractResult(text=text, method="pypdf", detail={"pages": page_count})

    # 文本层几乎没有内容 → 按扫描件处理，尝试 OCR 回退
    fallback = _ocr_pdf_pages(data, page_count)
    if fallback.ok:
        return fallback
    # OCR 也没成功：如果文本层多少有点内容，宁可先交出去（总比空字符串强）
    if text:
        fallback.text = text
        fallback.note = f"{fallback.note} 已回退到文本层抽到的 {len(text)} 个字符。"
    return fallback


# --------------------------------------------------------------------------- #
# DOCX / 文本
# --------------------------------------------------------------------------- #


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


def _extract_text(data: bytes) -> ExtractResult:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return ExtractResult(text=data.decode(encoding).strip(), method=f"text/{encoding}")
        except UnicodeDecodeError:
            continue
    return ExtractResult(text="", method="text", note="文本编码无法识别")


# --------------------------------------------------------------------------- #
# 图片与总入口
# --------------------------------------------------------------------------- #


def _extract_image(data: bytes) -> ExtractResult:
    result = ocr_service.recognize_bytes(data)
    detail = dict(result.meta)
    if result.backend:
        detail["ocr_backend"] = result.backend

    if not result.backend:  # 没有可用引擎
        return ExtractResult(text="", method="ocr-unavailable", note=result.note, detail=detail)
    if result.note and not result.text:  # 引擎跑了但没出文字（或直接失败）
        method = "ocr-failed" if result.note.startswith("OCR 识别失败") else f"ocr/{result.backend}"
        return ExtractResult(text="", method=method, note=result.note, detail=detail)
    return ExtractResult(text=result.text, method=f"ocr/{result.backend}", note=result.note, detail=detail)


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
