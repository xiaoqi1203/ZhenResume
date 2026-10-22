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


def _rows_from_words(words: list[dict]) -> list[list[dict]]:
    """把词按纵坐标聚成「行」。"""
    if not words:
        return []
    heights = [float(w.get("height") or 0) for w in words]
    char_h = max(1.0, sum(heights) / len(heights))
    tol = max(1.5, char_h * 0.55)
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        top = float(word["top"])
        if rows and abs(float(rows[-1][0]["top"]) - top) <= tol:
            rows[-1].append(word)
        else:
            rows.append([word])
    return rows


def _is_ascii_word(text: str) -> bool:
    return bool(text) and all(ord(ch) < 128 for ch in text)


def _join_words(chunk: list[dict]) -> str:
    """拼接一行的词：英文之间补空格，中文直接相连。"""
    text = ""
    for index, word in enumerate(chunk):
        piece = str(word.get("text") or "")
        if index > 0 and (_is_ascii_word(piece) or _is_ascii_word(chunk[index - 1].get("text") or "")):
            text += " "
        text += piece
    return text.strip()


def _row_to_lines(row_words: list[dict]) -> list[str]:
    """把一行词拼成文本。

    行内的**大间隔**用 ` ｜ ` 连接，而不是拆成两行 ——
    表格简历（左标签 / 右内容）正是靠这一行里的「标签 ｜ 值」配对；
    以前这里换行会把「姓名」和「李四」拆成两行，解析器就把标签当成了姓名。
    真正的双栏由 `_column_gap()` 先拆栏处理。
    """
    ordered = sorted(row_words, key=lambda w: float(w["x0"]))
    widths = [float(w["x1"]) - float(w["x0"]) for w in ordered]
    avg_w = max(1.0, sum(widths) / len(widths))
    gap_limit = max(avg_w * 2.4, 24.0)

    chunks: list[list[dict]] = [[ordered[0]]]
    for prev, word in zip(ordered, ordered[1:]):
        gap = float(word["x0"]) - float(prev["x1"])
        if gap > gap_limit:
            chunks.append([word])
        else:
            chunks[-1].append(word)

    line = " ｜ ".join(part for part in (_join_words(chunk) for chunk in chunks) if part)
    return [line] if line else []


def _column_gap(words: list[dict], page_width: float) -> float | None:
    """检测真正的**双栏**版面，返回栏间空白的 x 中心；单栏返回 None。

    判据（必须同时满足，否则会把「标签表格」误判成双栏）：
      1. 页面中部存在一条贯穿全页的空白竖带；
      2. 空白两侧的文本量都占 30% 以上（标签列通常只占 10% 左右）。
    """
    if not words or page_width <= 0 or len(words) < 20:
        return None

    bins = 100
    step = page_width / bins
    covered = [False] * bins
    for word in words:
        a = int(max(0, min(bins - 1, float(word["x0"]) // step)))
        b = int(max(0, min(bins - 1, float(word["x1"]) // step)))
        for i in range(a, b + 1):
            covered[i] = True

    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, is_covered in enumerate(covered):
        if not is_covered and run_start is None:
            run_start = i
        elif is_covered and run_start is not None:
            runs.append((run_start, i))
            run_start = None
    if run_start is not None:
        runs.append((run_start, bins))

    total = len(words)
    for start, end in runs:
        mid_ratio = (start + end) / 2 / bins
        if not (0.3 < mid_ratio < 0.7):
            continue
        split_x = mid_ratio * page_width
        left = sum(1 for w in words if (float(w["x0"]) + float(w["x1"])) / 2 < split_x)
        if left / total < 0.3 or (total - left) / total < 0.3:
            continue
        return split_x
    return None


def _page_lines(words: list[dict], page_width: float) -> list[str]:
    """把一页的词还原成文本行（双栏则先左栏后右栏）。"""
    gap_x = _column_gap(words, page_width)
    if gap_x is None:
        lines: list[str] = []
        for row in _rows_from_words(words):
            lines.extend(_row_to_lines(row))
        return lines

    left_words = [w for w in words if (float(w["x0"]) + float(w["x1"])) / 2 < gap_x]
    right_words = [w for w in words if (float(w["x0"]) + float(w["x1"])) / 2 >= gap_x]
    lines = []
    for band in (left_words, right_words):
        for row in _rows_from_words(band):
            lines.extend(_row_to_lines(row))
    return lines


def _pdf_text_layer_layout(data: bytes) -> tuple[str, int] | None:
    """用 pdfplumber 按版面坐标重建文本（解决双栏 / 表格式简历的字段错位）。

    返回 (文本, 页数)；pdfplumber 不可用或解析失败时返回 None，由调用方回退 pypdf。
    """
    try:
        import pdfplumber
    except Exception:  # noqa: BLE001 —— 没装就回退
        return None

    try:
        lines: list[str] = []
        page_count = 0
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                words = page.extract_words(keep_blank_chars=False, extra_attrs=["size"])
                lines.extend(_page_lines(words or [], float(page.width or 0)))
                lines.append("")
        text = "\n".join(lines).strip()
        return text, page_count
    except Exception:  # noqa: BLE001 —— 加密 / 损坏 / 不支持的 PDF
        return None


def _pdf_text_layer(data: bytes) -> tuple[str, int]:
    """返回 (文本层内容, 页数)。

    优先走**版面重建**（pdfplumber 坐标聚类）：pypdf 的 extract_text 只按内容流顺序拼接，
    双栏简历会把左右两栏交错成一段，章节标签与内容错位；坐标重建后再按行输出才与原文一致。
    版面重建失败或无文本时回退 pypdf。
    """
    laid_out = _pdf_text_layer_layout(data)
    if laid_out is not None and laid_out[0].strip():
        return laid_out

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
        return _finalize(_extract_pdf(data))
    if ext == ".docx":
        return _finalize(_extract_docx(data))
    if ext in IMAGE_EXTENSIONS:
        return _finalize(_extract_image(data))
    if ext in {".txt", ".md"}:
        return _finalize(_extract_text(data))
    return ExtractResult(text="", method="unsupported", note=f"不支持的文件类型：{ext}")


def _finalize(result: ExtractResult) -> ExtractResult:
    """抽取完成后的统一收尾：修正字形伪影 + 记录修正量。

    只在**抽取这一处**做，因此入库的 raw_text 从一开始就是干净文本 ——
    章节切分、证据 span、去重哈希、前端对照展示全部天然一致，无需各自打补丁。

    为什么是"静默但可观测"：修正的是肉眼同形的兼容字符（`⽬`→`目`），
    用户看到的文字没有任何变化；但修正量写进 `detail`，出问题时能查到。
    """
    from .textfold import fix_pdf_artifacts

    fixed = fix_pdf_artifacts(result.text or "")
    if fixed != result.text:
        result.detail = {**(result.detail or {}), "glyph_fixed": True}
        result.text = fixed
        if not result.note:
            result.note = "已自动修正 PDF 抽取产生的字形伪影（兼容字符），不影响文字内容。"
    return result
