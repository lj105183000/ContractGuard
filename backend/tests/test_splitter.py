from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import ParsedContract
from app.schemas import ClauseType


def test_split_articles_and_offsets(contract_text):
    parsed = ParsedContract(text=contract_text, source_type="txt")
    clauses = split_clauses(parsed)
    assert len(clauses) == 8
    assert clauses[0].clause_type == ClauseType.PREAMBLE
    assert any(
        clause.article == "第三条" and clause.clause_type == ClauseType.PAYMENT
        for clause in clauses
    )
    for clause in clauses:
        assert contract_text[clause.start : clause.end] == clause.text


def test_fallback_paragraphs_without_headings():
    text = (
        "甲方：某公司\n乙方：某供应商\n\n"
        "双方就采购事项达成如下约定，合同总价为人民币十万元整，验收合格后支付。\n\n"
        "乙方应在合同生效后三十日内完成交付，逾期按日承担违约责任。\n\n"
        "因本合同发生争议，由甲方所在地人民法院管辖。"
    )
    parsed = ParsedContract(text=text, source_type="txt")
    clauses = split_clauses(parsed)
    assert len(clauses) >= 2
    assert all(clause.text for clause in clauses)
    assert any("交付" in clause.text for clause in clauses)
    assert any("管辖" in clause.text for clause in clauses)
