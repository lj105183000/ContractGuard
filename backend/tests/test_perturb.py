import pytest

from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import ParsedContract
from eval.perturb import (
    add_ocr_noise,
    apply_text_perturbation,
    normalize_amount_formats,
    rename_parties,
    reorder_clauses,
)


def _parsed(text: str) -> ParsedContract:
    return ParsedContract(text=text, source_type="txt", page_count=0)


def test_rename_parties_replaces_names_consistently():
    text = "甲方：星海公司\n乙方：远航公司\n星海公司应向远航公司付款。"
    renamed = rename_parties(text)
    assert "星海公司" not in renamed
    assert "远航公司" not in renamed
    assert "某方公司1" in renamed
    assert "某方公司2" in renamed


def test_amount_formats_convert_yuan_and_percent_spacing():
    text = "合同总价 100 万元，预付款 50%。"
    converted = normalize_amount_formats(text)
    assert "1,000,000 元" in converted
    assert "50 %" in converted


def test_ocr_noise_is_deterministic_and_keeps_headings():
    text = "采购合同\n甲方：A；乙方：B。\n第一条 标的\n甲方向乙方采购设备一套。"
    first = add_ocr_noise(text, rate=1.0, seed=1)
    second = add_ocr_noise(text, rate=1.0, seed=1)
    assert first == second
    assert "第一条 标的" in first


def test_reorder_clauses_preserves_all_clause_articles():
    text = "第一条 标的\n设备。\n\n第二条 付款\n付款。\n\n第三条 交付\n交付。"
    parsed = _parsed(text)
    reordered = reorder_clauses(parsed)
    articles = [clause.article for clause in split_clauses(reordered)]
    assert articles == ["第一条", "第三条", "第二条"]


def test_unknown_perturbation_raises():
    with pytest.raises(ValueError):
        apply_text_perturbation(_parsed("正文"), "unknown")
