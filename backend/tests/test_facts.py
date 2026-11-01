from pathlib import Path

from app.parsing.clause_splitter import split_clauses
from app.parsing.contract_parser import parse_contract
from app.parsing.facts import _first_group, extract_heuristic_facts, merge_facts
from app.schemas import (
    ComplianceFacts,
    ContractFacts,
    DisputeFacts,
    TerminationFacts,
)

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "data" / "samples"


def test_heuristic_facts_on_sample_01():
    path = SAMPLES / "sample_01_数控设备采购合同.docx"
    parsed = parse_contract(path)
    clauses = split_clauses(parsed)
    facts = extract_heuristic_facts(parsed.text, clauses)
    assert facts.buyer == "星海智能装备有限公司"
    assert facts.supplier == "远航机电有限公司"
    assert facts.contract_value == 1_200_000
    assert facts.tax_included is True
    assert facts.payment.prepayment_ratio == 0.5
    assert facts.payment.payment_days == 30
    assert facts.payment.payment_after_acceptance is True
    assert facts.payment.full_payment_on_delivery is False
    assert facts.acceptance.period_days == 3
    assert facts.acceptance.deemed_acceptance is True
    assert facts.liability.penalty_daily_rate == 0.0001
    assert facts.liability.penalty_cap_ratio == 0.03
    assert facts.dispute.unfavorable_venue is True
    assert "乙方所在地" in (facts.dispute.court or "")


def test_payment_on_delivery_is_not_mistaken_for_after_acceptance():
    path = SAMPLES / "sample_02_办公家具采购合同.docx"
    parsed = parse_contract(path)
    clauses = split_clauses(parsed)
    facts = extract_heuristic_facts(parsed.text, clauses)
    assert facts.payment.payment_after_acceptance is True
    assert facts.payment.full_payment_on_delivery is False


def test_first_group_supports_patterns_without_capture_group():
    assert (
        _first_group(r"知识产权", "本合同知识产权归甲方所有")
        == "知识产权"
    )


def test_ip_ownership_clause_is_extracted():
    text = (
        "设备采购合同\n\n"
        "第八条 知识产权\n"
        "知识产权归甲方所有，乙方不得主张。\n"
    )
    facts = extract_heuristic_facts(text, [])
    assert facts.compliance.ip_ownership
    assert "知识产权" in facts.compliance.ip_ownership


def test_merge_facts_prefers_explicit_heuristic_protections():
    llm = ContractFacts(
        dispute=DisputeFacts(unfavorable_venue=True),
        termination=TerminationFacts(buyer_right=False, one_sided=True),
        compliance=ComplianceFacts(confidentiality_present=False),
    )
    heuristic = ContractFacts(
        dispute=DisputeFacts(unfavorable_venue=False),
        termination=TerminationFacts(buyer_right=True, one_sided=False),
        compliance=ComplianceFacts(confidentiality_present=True),
    )
    merged = merge_facts(llm, heuristic)
    assert merged.dispute.unfavorable_venue is False
    assert merged.termination.buyer_right is True
    assert merged.termination.one_sided is False
    assert merged.compliance.confidentiality_present is True


def test_merge_facts_does_not_turn_llm_detected_right_into_risk():
    llm = ContractFacts(
        termination=TerminationFacts(buyer_right=True, one_sided=False)
    )
    heuristic = ContractFacts(
        termination=TerminationFacts(buyer_right=False, one_sided=True)
    )
    merged = merge_facts(llm, heuristic)
    assert merged.termination.buyer_right is True
    assert merged.termination.one_sided is False


def test_merge_facts_keeps_llm_value_when_heuristic_is_unknown():
    llm = ContractFacts(dispute=DisputeFacts(unfavorable_venue=True))
    heuristic = ContractFacts(dispute=DisputeFacts(unfavorable_venue=None))
    merged = merge_facts(llm, heuristic)
    assert merged.dispute.unfavorable_venue is True
