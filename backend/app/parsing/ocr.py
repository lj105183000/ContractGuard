"""本地离线 OCR（RapidOCR + onnxruntime）。

只在遇到扫描页或图片时懒加载模型；模型随 Python 依赖一起安装，
桌面版由 PyInstaller 打包，运行时不访问网络。
"""

from __future__ import annotations

import threading
from typing import Any

from PIL import Image, ImageOps

from app.config import settings


class OcrError(RuntimeError):
    """OCR 运行失败的基类。"""


class OcrUnavailableError(OcrError):
    """OCR 组件未安装或模型无法加载。"""


_engine: Any = None
_engine_lock = threading.Lock()
_inference_lock = threading.Lock()


def _load_engine() -> Any:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr import RapidOCR
        except Exception as exc:  # noqa: BLE001
            raise OcrUnavailableError(
                f"OCR 组件未安装或加载失败：{exc}。请运行 uv sync 或重新安装桌面版。"
            ) from exc
        try:
            _engine = RapidOCR()
        except Exception as exc:  # noqa: BLE001
            raise OcrUnavailableError(f"OCR 模型初始化失败：{exc}") from exc
    return _engine


def prepare_image(image: Image.Image) -> Image.Image:
    """统一方向、色彩，并把超大图片缩到可控尺寸。"""
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    limit = settings.ocr_max_render_side
    if max(image.size) > limit:
        image = image.copy()
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    return image


def ocr_image(image: Image.Image) -> tuple[str, float | None]:
    """返回 (按行拼接的文本, 平均置信度)。没有识别到文字时返回 ("", None)。"""
    prepared = prepare_image(image)
    engine = _load_engine()
    # onnxruntime session 不保证并发安全；多个任务共用同一个引擎时串行推理。
    with _inference_lock:
        result = engine(prepared)

    texts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    lines: list[str] = []
    kept_scores: list[float] = []
    for text, score in zip(texts, scores):
        content = str(text or "").strip()
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            numeric = 0.0
        if content and numeric >= settings.ocr_min_score:
            lines.append(content)
            kept_scores.append(numeric)
    confidence = sum(kept_scores) / len(kept_scores) if kept_scores else None
    return "\n".join(lines), confidence
