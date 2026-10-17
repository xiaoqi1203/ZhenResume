"""本地 OCR 引擎层：**不需要任何外部 API / Key**，全部在本机推理。

为什么单独一个模块：`extract.py` 负责"按文件格式分派"，本模块只负责
"一张图片 → 一段文本"。PDF 的页渲染、内嵌图片抽取仍留在 `extract.py` 里编排。

引擎可插拔，按优先级探测（两个都装了就第一个可用者胜出）：

  1. rapidocr —— `pip install rapidocr-onnxruntime`。纯 pip、无系统组件，
     PP-OCR 中英文模型随 wheel 一起装，中文简历效果好，完全离线。
  2. tesseract —— 系统装 `tesseract-ocr`（+ `chi_sim` 语言包），pip 装 `pytesseract`。
     更轻，中文精度更依赖语言包质量与图像清晰度。

两个引擎都没有时**不报错**：返回带 `note` 的空结果，由上层走降级分支
（与 `extract.py` 的既有约定一致：不抛异常，失败信息放 note）。

与 `llm.py` 的对比：LLM 层要读 `ALIYUN_API_KEY` 之类的外部配置；本模块**刻意不引入
任何 Key / base_url 配置项** —— OCR 只依赖本机装了什么引擎，装了就自动启用。
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅为类型标注；运行时才导入 Pillow，缺它也不该让本模块 import 失败
    from PIL import Image

# 用户可见的"没有引擎"提示：短、可操作
NO_ENGINE_NOTE = (
    "未启用本地 OCR 引擎，无法识别图片简历。"
    "请改上传 PDF / DOCX 或直接粘贴文本；管理员可运行 `python -m scripts.check_ocr` 查看启用方式。"
)

# 给运维/自检看的完整启用方式（均为本地推理，不需要任何外部 API/Key）
ENABLE_HINT = (
    "二选一：\n"
    "  ① 推荐：pip install rapidocr-onnxruntime\n"
    "  ② 轻量：apt install -y tesseract-ocr tesseract-ocr-chi-sim && pip install pytesseract\n"
    "     （Windows：装 UB-Mannheim 版 tesseract 并勾选 Chinese Simplified，"
    "再 pip install pytesseract，必要时用 TESSERACT_CMD 指定路径）"
)


@dataclass
class OcrText:
    """一次识别的结果。`backend` 为空表示引擎没跑起来，原因在 `note` 里。"""

    text: str = ""
    backend: str = ""
    note: str | None = None
    elapsed_ms: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class BackendStatus:
    """自检 / 健康检查用的引擎探测结果。"""

    name: str
    available: bool
    detail: str
    hint: str = ""


class OcrBackend:
    """引擎接口：只做"图片 → 文本"，不碰文件格式。"""

    name = "base"

    def __init__(self) -> None:
        # 本次识别中因语言包缺失被降级掉的语种（由 recognize 填充）
        self.dropped_langs: list[str] = []

    def status(self) -> BackendStatus:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def prepare(self, image: "Image.Image") -> "Image.Image":
        """引擎各自的预处理偏好；默认原样返回。"""
        return image

    def recognize(self, image: "Image.Image") -> str:  # pragma: no cover - 由子类实现
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 引擎 1：rapidocr（纯 pip，无系统组件）
# --------------------------------------------------------------------------- #


class RapidOcrBackend(OcrBackend):
    name = "rapidocr"
    _engine = None  # 进程级单例：模型加载约 1~2s，每张图重建不可接受

    def status(self) -> BackendStatus:
        if importlib.util.find_spec("rapidocr_onnxruntime") is None:
            return BackendStatus(
                self.name, False, "未安装 rapidocr-onnxruntime", "pip install rapidocr-onnxruntime"
            )
        try:
            self._load()  # 真加载一次：onnxruntime 的 DLL 可能因缺 VC 运行库而失败
        except Exception as exc:  # noqa: BLE001
            return BackendStatus(
                self.name, False, f"引擎加载失败：{type(exc).__name__}: {str(exc)[:120]}", ENABLE_HINT
            )
        return BackendStatus(self.name, True, "onnxruntime 本地推理（PP-OCR 中英文，离线）")

    def _load(self):
        if RapidOcrBackend._engine is None:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            RapidOcrBackend._engine = RapidOCR()
        return RapidOcrBackend._engine

    def prepare(self, image: "Image.Image") -> "Image.Image":
        # RapidOCR 内部按 BGR 处理，给三通道彩色图更稳（灰度图它也能吃，但没必要丢信息）
        return image.convert("RGB")

    def recognize(self, image: "Image.Image") -> str:
        import numpy as np  # rapidocr 的依赖，随它一起装上

        from ..config import settings

        result, _elapse = self._load()(np.asarray(image))
        if not result:
            return ""
        threshold = settings.ocr_min_score
        lines: list[str] = []
        for item in result:
            # 1.x 返回结构：[box, text, score]
            if len(item) < 3:
                continue
            text, score = item[1], float(item[2])
            if text and score >= threshold:
                lines.append(str(text).strip())
        return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------- #
# 引擎 2：tesseract（系统二进制 + 薄封装）
# --------------------------------------------------------------------------- #


class TesseractBackend(OcrBackend):
    name = "tesseract"

    def status(self) -> BackendStatus:
        if importlib.util.find_spec("pytesseract") is None:
            return BackendStatus(self.name, False, "未安装 pytesseract", "pip install pytesseract")
        binary = self._binary()
        if not binary:
            return BackendStatus(
                self.name,
                False,
                "找不到 tesseract 可执行文件（PATH 里没有，TESSERACT_CMD 也未配置）",
                "apt install -y tesseract-ocr tesseract-ocr-chi-sim"
                "（Windows 装 UB-Mannheim 版，再用 TESSERACT_CMD 指定路径）",
            )
        langs = self._languages(binary)
        if not langs:
            return BackendStatus(
                self.name, False, f"tesseract 可执行（{binary}）但读不到语言包", "装 chi_sim / eng 语言包"
            )
        return BackendStatus(self.name, True, f"{binary}；语言包 {', '.join(sorted(langs))}")

    @staticmethod
    def _binary() -> str:
        import os

        from ..config import settings

        if settings.tesseract_cmd:
            return settings.tesseract_cmd if os.path.exists(settings.tesseract_cmd) else ""
        return shutil.which("tesseract") or ""

    @staticmethod
    @lru_cache(maxsize=4)
    def _languages(binary: str) -> frozenset[str]:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = binary
        try:
            return frozenset(pytesseract.get_languages(config=""))
        except Exception:  # noqa: BLE001 —— 二进制不可执行时按"无语言包"处理
            return frozenset()

    def prepare(self, image: "Image.Image") -> "Image.Image":
        # tesseract 在灰度图上更稳（少一层彩色噪声），也是其官方推荐做法
        return image.convert("L")

    def recognize(self, image: "Image.Image") -> str:
        import pytesseract

        from ..config import settings

        binary = self._binary()
        pytesseract.pytesseract.tesseract_cmd = binary or "tesseract"

        # 语言包缺失是最常见的坑：请求 chi_sim 但没装会直接抛错、整张图白跑。
        # 这里主动降级到"已安装语言里能用的子集"，并把降级结果交给上层写进 note。
        installed = self._languages(binary)
        requested = [lang for lang in (settings.ocr_lang or "eng").split("+") if lang]
        usable = [lang for lang in requested if lang in installed]
        self.dropped_langs = [lang for lang in requested if lang not in installed]
        if not usable:
            usable = ["eng"] if "eng" in installed else sorted(installed)[:1]
            self.dropped_langs = requested
        return pytesseract.image_to_string(image, lang="+".join(usable)).strip()


# --------------------------------------------------------------------------- #
# 引擎注册与选择
# --------------------------------------------------------------------------- #

ALL_BACKENDS: tuple[type[OcrBackend], ...] = (RapidOcrBackend, TesseractBackend)


def list_backends() -> list[BackendStatus]:
    """探测全部引擎。每次真探测（自检脚本 / 健康检查用），不走缓存。"""
    return [cls().status() for cls in ALL_BACKENDS]


def _resolve(prefer: str, enabled: bool) -> OcrBackend | None:
    if not enabled:
        return None
    wanted = (prefer or "auto").strip().lower()
    if wanted in {"off", "none", "disabled"}:
        return None
    for cls in ALL_BACKENDS:
        if wanted not in {"auto", ""} and cls.name != wanted:
            continue
        instance = cls()
        if instance.status().available:
            return instance
    return None


@lru_cache(maxsize=8)
def _resolve_cached(prefer: str, enabled: bool) -> OcrBackend | None:
    return _resolve(prefer, enabled)


def active_backend() -> OcrBackend | None:
    """当前生效的引擎；`ocr_backend=auto` 时按优先级取第一个可用的。"""
    from ..config import settings

    return _resolve_cached(settings.ocr_backend, settings.ocr_enabled)


def active_backend_name() -> str:
    backend = active_backend()
    return backend.name if backend else ""


def reset_cache() -> None:
    """配置变了（自检 / 测试里改 settings）时清缓存。"""
    _resolve_cached.cache_clear()
    TesseractBackend._languages.cache_clear()


def describe() -> list[dict]:
    """健康检查用的紧凑视图。"""
    return [
        {"name": s.name, "available": s.available, "detail": s.detail}
        for s in list_backends()
    ]


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #


def _prepare(image: "Image.Image") -> "Image.Image":
    """通用预处理：EXIF 转正 + 过大图等比缩小。

    刻意只做这两件"不改变内容"的事：二值化/去噪的参数依引擎与图片而异，
    盲调反而会把本来能识别的图弄坏。
    """
    from PIL import ImageOps

    from ..config import settings

    image = ImageOps.exif_transpose(image)
    limit = settings.ocr_max_side
    if limit and max(image.size) > limit:
        ratio = limit / float(max(image.size))
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image = image.resize(size)
    return image


def recognize_bytes(data: bytes) -> OcrText:
    """图片字节 → 文本。**不抛异常**：任何失败都落到 `note` 上。"""
    backend = active_backend()
    if backend is None:
        return OcrText(note=NO_ENGINE_NOTE)

    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as raw:
            image = _prepare(raw)
            started = time.perf_counter()
            text = backend.recognize(backend.prepare(image))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 —— 损坏图片 / 引擎崩溃都不该冒泡到接口层
        return OcrText(
            backend=backend.name, note=f"OCR 识别失败：{type(exc).__name__}: {str(exc)[:160]}"
        )

    meta: dict = {"elapsed_ms": elapsed_ms}
    if backend.dropped_langs:
        meta["lang_dropped"] = list(backend.dropped_langs)

    note = None
    if not text.strip():
        note = "OCR 未识别到文字（图片可能过小、模糊、倾斜或纯图形）。"
    elif backend.dropped_langs:
        note = f"语言包缺失：{', '.join(backend.dropped_langs)} 未安装，已用其余语言识别。"
    return OcrText(
        text=text.strip(), backend=backend.name, note=note, elapsed_ms=elapsed_ms, meta=meta
    )
