"""OCR 自检：确认本机 OCR 引擎能不能用，并**真跑一遍**图片与扫描件。

用法（在 backend 目录下）：

    python -m scripts.check_ocr                # 探测引擎 + 真跑合成样本
    python -m scripts.check_ocr --keep         # 保留生成的图片/PDF，便于肉眼比对
    python -m scripts.check_ocr --image 某图.png   # 顺手识别一张真实图片

退出码 0 = OCR 可用且端到端跑通；非 0 = 没引擎或识别不出中文（**别指望图片简历能解析**）。

本项目**不需要任何外部 OCR API / Key**：引擎全部在本机推理。
两个可选引擎（装任意一个即可，装了自动启用）：
  ① rapidocr —— pip install rapidocr-onnxruntime   （纯 pip，中文效果好，推荐）
  ② tesseract —— apt install -y tesseract-ocr tesseract-ocr-chi-sim && pip install pytesseract
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sample_images import find_cjk_font, render_resume_image  # noqa: E402

# Windows 控制台默认是 GBK，中文提示里的 ⚠️ / ✅ 会直接抛 UnicodeEncodeError。
# 自检脚本必须"怎么都能把话说出来"，所以退化为替换字符而不是崩掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

RESULTS: list[tuple[str, bool, str]] = []

SAMPLE_TEXT = """张伟
后端开发工程师 | 138-1234-5678 | zhangwei@example.com

工作经历
杭州某科技有限公司 | 后端开发工程师 2022.07 - 至今

专业技能
Python、Go、MySQL、Redis、Kafka、Docker
"""

# 中文识别正确的判据：这些词必须出现（不要求 100% 一字不差，OCR 天然有噪声）
EXPECT_KEYWORDS = ("工作经历", "专业技能", "Python", "138")


def check(name: str, ok: bool, hint: str = "") -> bool:
    RESULTS.append((name, bool(ok), hint))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {hint}" if hint and not ok else ""))
    return bool(ok)


def check_config() -> None:
    from app.config import settings

    print(f"      ocr_enabled={settings.ocr_enabled}  ocr_backend={settings.ocr_backend}  "
          f"ocr_lang={settings.ocr_lang}  ocr_max_pages={settings.ocr_max_pages}")
    if not settings.ocr_enabled:
        print("      ⚠️ OCR_ENABLED=false：图片与扫描件会一律走降级分支")


def check_backends() -> bool:
    from app.services import ocr

    statuses = ocr.list_backends()
    any_available = False
    for status in statuses:
        any_available = any_available or status.available
        check(
            f"引擎 {status.name}",
            status.available,
            f"{status.detail}\n           装：{status.hint or ocr.ENABLE_HINT}",
        )
    active = ocr.active_backend_name()
    check("已选定生效引擎", bool(active), f"没有可用引擎。{ocr.ENABLE_HINT}")
    if active:
        print(f"      当前生效：{active}")
    return any_available


def _run_extract(data: bytes, filename: str, label: str) -> str:
    from app.services import extract

    result = extract.extract_text(data, filename)
    print(f"      {label}: method={result.method}  {len(result.text)} 字  detail={result.detail}")
    if result.note:
        print(f"      note: {result.note}")
    return result.text


def check_image(workdir: Path, keep: bool) -> None:
    """合成一张中文简历图 → 走完整抽取链路 → 校验识别结果。"""
    image = render_resume_image(SAMPLE_TEXT)
    png = workdir / "ocr_check.png"
    image.save(png)
    text = _run_extract(png.read_bytes(), png.name, "图片")

    if not find_cjk_font():
        print("[SKIP] 未找到中文字体，合成的图片里中文是方框，跳过中文断言")
        return
    missing = [word for word in EXPECT_KEYWORDS if word not in text]
    check("图片 OCR 识别出中文关键内容", not missing, f"未识别到：{missing}")
    if keep:
        print(f"      已保留图片：{png}")


def check_scanned_pdf(workdir: Path, keep: bool) -> None:
    """扫描版 PDF（只有图像、没有文本层）→ 校验"文本层为空 → 页图 → OCR"这条回退链。"""
    from app.services import extract

    image = render_resume_image(SAMPLE_TEXT)
    pdf = workdir / "ocr_check_scan.pdf"
    image.save(pdf, "PDF", resolution=150.0)

    # 先确认它确实**没有**文本层，否则这个用例没验证到回退分支
    layer, pages = extract._pdf_text_layer(pdf.read_bytes())
    check("样本 PDF 确实没有文本层（是扫描件）", len(layer) == 0, f"抽到 {len(layer)} 字，样本不成立")

    text = _run_extract(pdf.read_bytes(), pdf.name, "扫描版 PDF")
    if not find_cjk_font():
        print("[SKIP] 未找到中文字体，跳过中文断言")
        return
    missing = [word for word in EXPECT_KEYWORDS if word not in text]
    check("扫描版 PDF 回退 OCR 成功", not missing, f"未识别到：{missing}（共 {pages} 页）")
    if keep:
        print(f"      已保留 PDF：{pdf}")


def check_real_image(path: Path) -> None:
    from app.services import extract

    result = extract.extract_text(path.read_bytes(), path.name)
    print(f"      {path.name}: method={result.method}  {len(result.text)} 字")
    print("      " + (result.text[:200].replace("\n", " / ") or "(无文本)"))
    check(f"真实图片可抽取：{path.name}", result.ok, result.note or "识别为空")


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 自检（本地引擎，无需外部 API）")
    parser.add_argument("--keep", action="store_true", help="保留生成的图片/PDF")
    parser.add_argument("--image", default="", help="额外识别一张真实图片")
    args = parser.parse_args()

    print("=" * 70)
    print("OCR 自检（全部在本机推理，不需要任何外部 API / Key）")
    print("=" * 70)

    check_config()
    available = check_backends()

    if available:
        workdir = Path(tempfile.mkdtemp(prefix="zhenresume-ocr-"))
        if args.keep:
            workdir = Path.cwd() / "data" / "ocr-check"
            workdir.mkdir(parents=True, exist_ok=True)
        check_image(workdir, args.keep)
        check_scanned_pdf(workdir, args.keep)
        if not args.keep:
            for leftover in workdir.glob("ocr_check*"):
                leftover.unlink(missing_ok=True)
    else:
        print("\n⚠️ 没有任何 OCR 引擎，图片简历与扫描件只能降级（返回空文本 + note）。")
        print("   装一个即可自动启用：")
        print("     pip install rapidocr-onnxruntime")

    if args.image:
        check_real_image(Path(args.image))

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        print("未通过：")
        for name, ok, hint in RESULTS:
            if not ok:
                print(f"  ✗ {name}" + (f"\n      {hint}" if hint else ""))
        return 1
    print("✅ OCR 可用，图片简历与扫描版 PDF 均能解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
