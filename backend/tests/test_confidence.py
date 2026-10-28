from app.schemas import (
    Category,
    ConfidenceTier,
    Finding,
    FindingStatus,
    Severity,
    Source,
)
from app.services.confidence import apply_confidence_tier, classify_finding


def _finding(**overrides):
    data = {
        "id": "f1",
        "category": Category.PAYMENT,
        "severity": Severity.HIGH,
        "source": Source.LLM,
        "title": "预付款比例过高",
    }
    data.update(overrides)
    return Finding(**data)


def test_rule_finding_is_confirmed():
    finding = _finding(source=Source.RULE, rule_id="PAY-001")
    tier, reasons = classify_finding(finding)
    assert tier == ConfidenceTier.CONFIRMED
    assert any("规则引擎命中" in reason for reason in reasons)


def test_rule_llm_finding_is_confirmed():
    finding = _finding(source=Source.RULE_LLM, rule_id="PAY-001")
    assert classify_finding(finding)[0] == ConfidenceTier.CONFIRMED


def test_rule_with_unlocatable_quote_is_needs_review():
    finding = _finding(
        source=Source.RULE,
        rule_id="PAY-001",
        evidence_quote="合同原文",
        evidence_start=None,
    )
    assert classify_finding(finding)[0] == ConfidenceTier.NEEDS_REVIEW


def test_llm_with_evidence_and_law_is_advisory():
    finding = _finding(
        evidence_quote="支付预付款50%",
        evidence_start=0,
        evidence_end=8,
        legal_basis_ids=["CC-509"],
    )
    tier, reasons = classify_finding(finding)
    assert tier == ConfidenceTier.ADVISORY
    assert any("未获规则引擎佐证" in reason for reason in reasons)


def test_llm_without_law_is_needs_review():
    finding = _finding(
        evidence_quote="支付预付款50%",
        evidence_start=0,
        evidence_end=8,
    )
    assert classify_finding(finding)[0] == ConfidenceTier.NEEDS_REVIEW


def test_llm_without_evidence_is_needs_review():
    finding = _finding(legal_basis_ids=["CC-509"])
    assert classify_finding(finding)[0] == ConfidenceTier.NEEDS_REVIEW


def test_model_confidence_does_not_change_tier():
    low = _finding(confidence=0.01, evidence_quote="原文", evidence_start=0, evidence_end=2)
    high = _finding(confidence=0.99, evidence_quote="原文", evidence_start=0, evidence_end=2)
    assert classify_finding(low)[0] == classify_finding(high)[0]


def test_needs_review_becomes_needs_human():
    finding = _finding(confidence=0.9)
    apply_confidence_tier(finding)
    assert finding.confidence_tier == ConfidenceTier.NEEDS_REVIEW
    assert finding.status == FindingStatus.NEEDS_HUMAN
    assert finding.confidence <= 0.3
