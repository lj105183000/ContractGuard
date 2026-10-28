from app.graph.nodes.aggregate import _merge_findings
from app.schemas import Category, Finding, Severity, Source


def _finding(finding_id, **overrides):
    data = {
        "id": finding_id,
        "clause_id": "c1",
        "category": Category.PAYMENT,
        "severity": Severity.MEDIUM,
        "source": Source.LLM,
        "title": "预付款比例过高",
        "evidence_quote": "合同生效后支付预付款50%",
        "evidence_start": 10,
        "evidence_end": 23,
    }
    data.update(overrides)
    return Finding(**data)


def test_llm_finding_merges_into_overlapping_rule_finding():
    rule = _finding(
        "rule-1",
        source=Source.RULE,
        rule_id="PAY-001",
        severity=Severity.HIGH,
        evidence_quote="支付预付款50%",
        evidence_start=14,
        evidence_end=22,
        legal_basis_ids=["CC-509"],
    )
    llm = _finding(
        "llm-1",
        source=Source.LLM,
        rule_hint="PAY-001",
        evidence_quote="合同生效后支付预付款50%",
        legal_basis_ids=["CC-509", "CC-510"],
    )
    merged = _merge_findings([rule, llm])
    assert len(merged) == 1
    result = merged[0]
    assert result.id == "rule-1"
    assert result.source == Source.RULE_LLM
    assert result.severity == Severity.HIGH
    assert set(result.legal_basis_ids) == {"CC-509", "CC-510"}
    assert any("合并" in note for note in result.gate_notes)


def test_non_overlapping_findings_are_not_merged():
    rule = _finding(
        "rule-1",
        source=Source.RULE,
        rule_id="PAY-001",
        evidence_quote="支付预付款50%",
        evidence_start=14,
        evidence_end=22,
    )
    llm = _finding(
        "llm-1",
        evidence_quote="尾款应在验收后30日内支付",
        evidence_start=40,
        evidence_end=55,
    )
    merged = _merge_findings([rule, llm])
    assert len(merged) == 2


def test_two_llm_findings_with_same_title_merge():
    first = _finding("llm-1", evidence_quote="预付款50%", evidence_start=10, evidence_end=15)
    second = _finding(
        "llm-2",
        evidence_quote="预付款比例为百分之五十",
        evidence_start=80,
        evidence_end=92,
        severity=Severity.HIGH,
    )
    merged = _merge_findings([first, second])
    assert len(merged) == 1
    assert merged[0].severity == Severity.HIGH


def test_rejected_citation_ids_survive_merge():
    rule = _finding("rule-1", source=Source.RULE, rule_id="PAY-001")
    rule.rejected_legal_basis_ids = ["FAKE-1"]
    llm = _finding("llm-1", rejected_legal_basis_ids=["FAKE-2"])
    merged = _merge_findings([rule, llm])
    assert set(merged[0].rejected_legal_basis_ids) == {"FAKE-1", "FAKE-2"}
