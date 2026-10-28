"""把 PDF / DOCX / TXT / 图片转成纯文本，并保留页码信息。

PDF 优先使用文字层；扫描页（以及 JPG/PNG 图片）走本地 RapidOCR。
OCR 结果会标记 used_ocr，由上层写入运行提示，提醒人工核对。
"""

from __future__ import annotations

import logging
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image
from pypdf import PdfReader

from app.config import settings
from app.parsing import ocr

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"} | IMAGE_EXTENSIONS

ProgressCallback = Callable[[int, int], None]


class ContractParseError(ValueError):
    """合同解析失败的基类。"""


class UnsupportedFileError(ContractParseError):
    """文件类型不支持。"""


class ScannedDocumentError(ContractParseError):
    """扫描件/图片无法通过 OCR 提取文字。"""


# 兼容旧名称；扫描件不再只限 PDF。
ScannedPdfError = ScannedDocumentError


@dataclass
class ParsedContract:
    text: str
    source_type: str
    page_count: int = 0
    pages: list[str] = field(default_factory=list)
    page_offsets: list[int] = field(default_factory=list)
    title: str | None = None
    used_ocr: bool = False
    ocr_pages: int = 0
    ocr_failed_pages: int = 0
    ocr_confidence: float | None = None


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "").replace("​", "").replace("‌", "")
    for space in (" ", "　", " ", " "):
        text = text.replace(space, " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip(" #\t")
        if not line:
            continue
        if 2 <= len(line) <= 80 and ("合同" in line or "协议" in line):
            return line
        if len(line) <= 40:
            return line
        break
    return fallback


def _iter_docx_blocks(document):  # type: ignore[no-untyped-def]
    """按文档中的真实顺序产出段落和表格。"""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _parse_docx(path: Path, filename: str) -> ParsedContract:
    try:
        with zipfile.ZipFile(path) as archive:
            uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ContractParseError("DOCX 文件损坏或不是有效的压缩包。") from exc
    limit = settings.max_docx_uncompressed_mb * 1024 * 1024
    if uncompressed > limit:
        raise ContractParseError(
            f"DOCX 解压后超过 {settings.max_docx_uncompressed_mb}MB 限制。"
        )
    document = Document(str(path))
    parts: list[str] = []
    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                parts.append(text)
        else:
            for row in block.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    raw = "\n".join(parts)
    text = _normalize(raw)
    if len(text) < 20:
        raise ContractParseError("DOCX 中没有可读取的文字，请检查文件是否为空。")
    return ParsedContract(
        text=text,
        source_type="docx",
        page_count=0,
        title=_guess_title(text, Path(filename).stem),
    )


def _page_has_image(page) -> bool:  # type: ignore[no-untyped-def]
    """混合 PDF 中只 OCR 带图片的低文本页，避免把空白页也送去识别。"""
    try:
        return bool(list(page.images))
    except Exception:  # noqa: BLE001
        return True


def _render_pdf_page(pdf, index: int) -> Image.Image:  # type: ignore[no-untyped-def]
    page = pdf[index]
    width, height = page.get_size()
    longest = max(width, height, 1.0)
    scale = min(settings.ocr_render_scale, settings.ocr_max_render_side / longest)
    bitmap = page.render(scale=max(scale, 0.05))
    try:
        # to_pil() 的数据可能依赖 bitmap，复制后再关闭渲染对象。
        return bitmap.to_pil().copy()
    finally:
        bitmap.close()


def _parse_pdf(
    path: Path,
    filename: str,
    progress: ProgressCallback | None = None,
) -> ParsedContract:
    try:
        reader = PdfReader(str(path))
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ContractParseError("PDF 已加密，无法读取。") from exc

        page_count = len(reader.pages)
        if page_count == 0:
            raise ContractParseError("PDF 没有页面。")
        if page_count > settings.max_pdf_pages:
            raise ContractParseError(
                f"PDF 有 {page_count} 页，超过上限 {settings.max_pdf_pages} 页。"
            )
        raw_pages: list[str] = []
        for page in reader.pages:
            try:
                raw_pages.append(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001
                logger.warning("PDF 第 %s 页文字提取失败：%s", len(raw_pages) + 1, exc)
                raw_pages.append("")
    except ContractParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ContractParseError(f"PDF 读取失败：{exc}") from exc

    normalized = [_normalize(page) for page in raw_pages]
    total_chars = sum(len(page) for page in normalized)

    if total_chars < 50:
        # pypdf 对部分中文字体支持一般，换 pdfplumber 再试一次。
        try:
            with pdfplumber.open(str(path)) as pdf:
                candidate = [_normalize(page.extract_text() or "") for page in pdf.pages]
            if len(candidate) == page_count:
                candidate_chars = sum(len(page) for page in candidate)
                if candidate_chars > total_chars:
                    normalized = candidate
                    total_chars = candidate_chars
        except Exception:  # noqa: BLE001
            pass

    low_pages = [
        index
        for index, page in enumerate(normalized)
        if len(page.strip()) < settings.ocr_min_page_chars
    ]
    ocr_candidates: list[int] = []
    if low_pages:
        if total_chars < 50:
            # 整份文档几乎没有文字层，按扫描件处理。
            ocr_candidates = low_pages
        else:
            # 混合 PDF：只补识别带图片的低文本页。
            ocr_candidates = [
                index for index in low_pages if _page_has_image(reader.pages[index])
            ]

    if len(ocr_candidates) > settings.ocr_max_pages:
        raise ContractParseError(
            f"扫描件中需要 OCR 的页面有 {len(ocr_candidates)} 页，"
            f"超过上限 {settings.ocr_max_pages} 页。请拆分文件，"
            "或调高 OCR_MAX_PAGES。"
        )

    used_pages = 0
    failed_pages = 0
    unavailable_error: str | None = None
    confidences: list[float] = []
    if ocr_candidates:
        if progress:
            progress(0, len(ocr_candidates))
        try:
            with pdfium.PdfDocument(
                str(path), password="" if encrypted else None
            ) as pdf:
                if len(pdf) != page_count:
                    logger.warning(
                        "PDF 页数不一致：pypdf=%s pypdfium=%s", page_count, len(pdf)
                    )
                for done, index in enumerate(ocr_candidates, 1):
                    try:
                        if index >= len(pdf):
                            raise ValueError("页面不存在")
                        image = _render_pdf_page(pdf, index)
                        text, confidence = ocr.ocr_image(image)
                        if text.strip():
                            normalized[index] = _normalize(text)
                            used_pages += 1
                            if confidence is not None:
                                confidences.append(confidence)
                    except Exception as exc:  # noqa: BLE001
                        failed_pages += 1
                        if isinstance(exc, ocr.OcrUnavailableError):
                            unavailable_error = str(exc)
                        logger.warning("OCR 第 %s 页失败：%s", index + 1, exc)
                    if progress:
                        progress(done, len(ocr_candidates))
        except Exception as exc:  # noqa: BLE001
            raise ContractParseError(f"扫描件 OCR 失败：{exc}") from exc

    if used_pages == 0 and unavailable_error:
        raise ContractParseError(unavailable_error)

    text = "\n".join(normalized)
    if len(text.strip()) < 50:
        raise ScannedDocumentError(
            "扫描件 OCR 未识别出有效文字。请上传更清晰、端正的扫描件，"
            "或改用可复制文字的 PDF/DOCX。"
        )

    parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for page in normalized:
        offsets.append(cursor)
        parts.append(page)
        cursor += len(page) + 1
    text = "\n".join(parts)

    title = None
    try:
        title = (reader.metadata.title or "").strip() or None  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        title = None

    return ParsedContract(
        text=text,
        source_type="pdf",
        page_count=page_count,
        pages=normalized,
        page_offsets=offsets,
        title=title or _guess_title(text, Path(filename).stem),
        used_ocr=used_pages > 0,
        ocr_pages=used_pages,
        ocr_failed_pages=failed_pages,
        ocr_confidence=(
            sum(confidences) / len(confidences) if confidences else None
        ),
    )


def _decode_txt(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_txt(path: Path, filename: str) -> ParsedContract:
    text = _normalize(_decode_txt(path.read_bytes()))
    if len(text) < 20:
        raise ContractParseError("TXT 文件内容为空或过短。")
    return ParsedContract(
        text=text,
        source_type="txt",
        title=_guess_title(text, Path(filename).stem),
    )


def _parse_image(path: Path, filename: str) -> ParsedContract:
    try:
        with Image.open(path) as image:
            text, confidence = ocr.ocr_image(image)
    except ocr.OcrUnavailableError as exc:
        raise ContractParseError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ContractParseError(f"图片读取或 OCR 失败：{exc}") from exc

    text = _normalize(text)
    if len(text) < 20:
        raise ScannedDocumentError(
            "图片中没有识别到文字。请上传更清晰、端正的扫描件。"
        )
    return ParsedContract(
        text=text,
        source_type="image",
        page_count=1,
        pages=[text],
        page_offsets=[0],
        title=_guess_title(text, Path(filename).stem),
        used_ocr=True,
        ocr_pages=1,
        ocr_confidence=confidence,
    )


def parse_contract(
    path: Path,
    filename: str | None = None,
    progress: ProgressCallback | None = None,
) -> ParsedContract:
    """按扩展名解析合同文件；扫描 PDF/图片会走 OCR 回退。"""
    if not path.exists():
        raise ContractParseError("文件不存在。")
    filename = filename or path.name
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileError(
            f"不支持的文件类型 {suffix or '(无扩展名)'}；"
            "请上传 PDF、DOCX、TXT 或 JPG/PNG 图片。"
        )
    if suffix == ".pdf":
        return _parse_pdf(path, filename, progress)
    if suffix == ".docx":
        return _parse_docx(path, filename)
    if suffix == ".txt":
        return _parse_txt(path, filename)
    return _parse_image(path, filename)
