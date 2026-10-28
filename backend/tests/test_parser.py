from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.parsing import contract_parser, ocr
from app.parsing.contract_parser import (
    ContractParseError,
    UnsupportedFileError,
    parse_contract,
)

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "data" / "samples"

OCR_TEXT = (
    "第一条 合同标的\n"
    "甲方向乙方采购数控设备一套，合同总价为人民币100万元，"
    "验收合格后30日内支付尾款。"
)


def test_parse_docx_uncompressed_cap():
    old = settings.max_docx_uncompressed_mb
    object.__setattr__(settings, "max_docx_uncompressed_mb", 0)
    try:
        with pytest.raises(ContractParseError, match="解压后"):
            parse_contract(SAMPLES / "sample_01_数控设备采购合同.docx")
    finally:
        object.__setattr__(settings, "max_docx_uncompressed_mb", old)


def test_parse_pdf_page_cap():
    old = settings.max_pdf_pages
    object.__setattr__(settings, "max_pdf_pages", 0)
    try:
        with pytest.raises(ContractParseError, match="超过上限"):
            parse_contract(SAMPLES / "sample_05_食品包装材料采购合同.pdf")
    finally:
        object.__setattr__(settings, "max_pdf_pages", old)


def test_parse_docx_sample():
    path = SAMPLES / "sample_01_数控设备采购合同.docx"
    parsed = parse_contract(path)
    assert parsed.source_type == "docx"
    assert "数控加工中心" in parsed.text
    assert "星海智能装备有限公司" in parsed.text


def test_parse_pdf_sample_has_chinese_text():
    path = SAMPLES / "sample_05_食品包装材料采购合同.pdf"
    parsed = parse_contract(path)
    assert parsed.source_type == "pdf"
    assert parsed.page_count >= 1
    assert "食品级塑料包装盒" in parsed.text
    assert "预付款" in parsed.text


def test_parse_txt_gb18030(tmp_path):
    path = tmp_path / "gbk_contract.txt"
    path.write_bytes("合同总价为人民币100万元，验收合格后支付。".encode("gb18030"))
    parsed = parse_contract(path)
    assert "合同总价" in parsed.text
    assert parsed.source_type == "txt"


def test_parse_empty_txt_raises(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ContractParseError):
        parse_contract(path)


def test_unsupported_extension(tmp_path):
    path = tmp_path / "contract.exe"
    path.write_text("not a contract", encoding="utf-8")
    with pytest.raises(UnsupportedFileError):
        parse_contract(path)


def test_parse_scanned_pdf_uses_ocr(scanned_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_image", lambda image: (OCR_TEXT, 0.95))

    parsed = parse_contract(scanned_pdf)

    assert parsed.source_type == "pdf"
    assert parsed.used_ocr is True
    assert parsed.ocr_pages == 1
    assert parsed.page_count == 1
    assert parsed.ocr_confidence == 0.95
    assert "合同标的" in parsed.text


def test_parse_image_uses_ocr(scan_image, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_image", lambda image: (OCR_TEXT, 0.9))

    parsed = parse_contract(scan_image)

    assert parsed.source_type == "image"
    assert parsed.used_ocr is True
    assert parsed.ocr_pages == 1
    assert parsed.page_count == 1
    assert "合同标的" in parsed.text


def test_mixed_pdf_ocrs_low_text_image_page(tmp_path, monkeypatch):
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(image_path)
    pdf_path = tmp_path / "mixed.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(
        72,
        760,
        "First page text: payment within 30 days after acceptance and delivery.",
    )
    pdf.showPage()
    pdf.drawImage(str(image_path), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()

    monkeypatch.setattr(ocr, "ocr_image", lambda image: (OCR_TEXT, 0.9))

    parsed = parse_contract(pdf_path)

    assert parsed.page_count == 2
    assert parsed.used_ocr is True
    assert parsed.ocr_pages == 1
    assert "First page text" in parsed.pages[0]
    assert "合同标的" in parsed.pages[1]


def test_scanned_pdf_page_limit(tmp_path, monkeypatch):
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    image_path = tmp_path / "page.png"
    Image.new("RGB", (200, 150), "white").save(image_path)
    pdf_path = tmp_path / "two_pages.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    for _ in range(2):
        pdf.drawImage(str(image_path), 0, 0, width=A4[0], height=A4[1])
        pdf.showPage()
    pdf.save()

    monkeypatch.setattr(contract_parser, "settings", replace(settings, ocr_max_pages=1))

    with pytest.raises(ContractParseError, match="超过上限 1 页"):
        parse_contract(pdf_path)
