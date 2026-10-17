"""把 .txt 简历样本渲染成**图片样本**（PNG + 扫描版 PDF），供 OCR 回归使用。

为什么需要它：契约声明支持 PNG / JPG 与扫描件，而这两个分支必须能被**可复现地**
验证。往仓库里塞一张来路不明的截图，没法回答"这张图是怎么来的、内容变了图会不会过期"。
所以图片样本和 DOCX / PDF 样本一样，由 .txt 现场生成。

生成的两种文件对应两条真实链路：
- `*.png`            —— 手机拍照 / 截图上传，走"图片 → OCR"；
- `*_scan.pdf`       —— 只有图像、**没有文本层**的 PDF，模拟扫描件，
                        走"PDF 文本层为空 → 内嵌位图 → OCR"。

用法：见 `make_sample_docs.py --images`。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SECTION_HEADINGS = {
    "个人信息",
    "个人优势",
    "工作经历",
    "项目经历",
    "专业技能",
    "教育经历",
    "证书",
    "证书 / 荣誉",
    "自我评价",
}

# 常见中文字体（跨平台）。找不到就退回 PIL 内置位图字体，但那样中文会变方框，
# 调用方应据此跳过"中文识别正确性"的断言。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

PAGE_WIDTH = 1240          # ≈ A4 @150dpi
MARGIN = 70
LINE_GAP = 10


def find_cjk_font() -> str:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


def _font(path: str, size: int) -> ImageFont.ImageFont:
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """按像素宽度折行（中文没有空格，必须逐字量宽）。"""
    lines: list[str] = []
    current = ""
    for char in text:
        probe = current + char
        if draw.textlength(probe, font=font) <= max_width or not current:
            current = probe
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def render_resume_image(text: str, font_path: str = "") -> Image.Image:
    """把线性简历文本渲染成一张"看起来像简历"的图片。"""
    font_path = font_path or find_cjk_font()
    title_font = _font(font_path, 40)
    heading_font = _font(font_path, 27)
    body_font = _font(font_path, 22)

    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    max_width = PAGE_WIDTH - MARGIN * 2

    # 先排版算出高度，再画：避免固定画布把长简历截断
    blocks: list[tuple[ImageFont.ImageFont, list[str], int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in SECTION_HEADINGS:
            font, gap = heading_font, 18
        elif len(blocks) == 0:
            font, gap = title_font, 10
        else:
            font, gap = body_font, LINE_GAP
        blocks.append((font, _wrap(scratch, line, font, max_width), gap))

    total = MARGIN * 2 + sum(
        sum(int(font.size * 1.45) if hasattr(font, "size") else 16 for _ in wrapped) + gap + 6
        for font, wrapped, gap in blocks
    )
    canvas = Image.new("RGB", (PAGE_WIDTH, max(MARGIN * 2 + 40, total)), "white")
    draw = ImageDraw.Draw(canvas)

    y = MARGIN
    for font, wrapped, gap in blocks:
        line_height = int(font.size * 1.45) if hasattr(font, "size") else 16
        for item in wrapped:
            draw.text((MARGIN, y), item, font=font, fill=(25, 25, 30))
            y += line_height
        y += gap + 6
    return canvas


def render_samples(txt_path: Path, png_path: Path, scan_pdf_path: Path, dpi: int = 150) -> dict:
    """由 .txt 生成 PNG 与"只有图像"的扫描版 PDF。返回生成信息。"""
    text = txt_path.read_text(encoding="utf-8")
    image = render_resume_image(text)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_path)

    # PIL 存 PDF = 每页一张位图、没有文本层 —— 正好是扫描件的形态
    image.save(scan_pdf_path, "PDF", resolution=float(dpi))
    return {
        "png": png_path,
        "pdf": scan_pdf_path,
        "size": image.size,
        "cjk_font": bool(find_cjk_font()),
    }
