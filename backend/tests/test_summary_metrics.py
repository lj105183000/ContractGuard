from app.schemas import (
    Category,
    ConfidenceTier,
    Finding,
    FindingStatus,
    Severity,
    Source,
)
from app.services.summary import (
    build_summary,
    compute_citation_metrics,
    compute_evidence_metrics,
)


def _finding(finding_id, **overrides):
    data = {
        "id": finding_id,
        "category": Category.PAYMENT,
        "severity": Severity.MEDIUM,
        "source": Source.RULE,
        "title": "风险",
    }
    data.update(overrides)
    return Finding(**data)


def test_evidence_metric_excludes_rule_without_quote():
    no_quote = _finding("r1", rule_id="ACC-001")
    located = _finding(
        "l1",
        source=Source.LLM,
        evidence_quote="原文",
        evidence_start=0,
        evidence_end=2,
    )
    missing = _finding("l2", source=Source.LLM, evidence_quote="无法定位")
    checked, valid, rate = compute_evidence_metrics([no_quote, located, missing])
    assert checked == 2
    assert valid == 1
    assert rate == 0.5


def test_citation_metric_counts_rejected_ids():
    finding = _finding(
        "r1",
        legal_basis_ids=["CC-509"],
        rejected_legal_basis_ids=["FAKE-1"],
    )
    checked, valid, rate = compute_citation_metrics([finding])
    assert checked == 2
    assert valid == 1
    assert rate == 0.5


def test_summary_counts_sources_and_tiers():
    confirmed = _finding(
        "r1",
        source=Source.RULE,
        rule_id="PAY-001",
        severity=Severity.HIGH,
        confidence_tier=ConfidenceTier.CONFIRMED,
    )
    advisory = _finding(
        "l1",
        source=Source.LLM,
        severity=Severity.HIGH,
        confidence_tier=ConfidenceTier.ADVISORY,
    )
    needs_review = _finding(
        "l2",
        source=Source.LLM,
        severity=Severity.HIGH,
        status=FindingStatus.NEEDS_HUMAN,
        confidence_tier=ConfidenceTier.NEEDS_REVIEW,
    )
    merged = _finding(
        "m1",
        source=Source.RULE_LLM,
        confidence_tier=ConfidenceTier.CONFIRMED,
    )
    summary = build_summary([confirmed, advisory, needs_review, merged])
    assert summary.by_source == {"rule": 1, "llm": 2, "rule+llm": 1}
    assert summary.by_confidence == {"confirmed": 2, "advisory": 1, "needs_review": 1}
    assert summary.blocking_high_pending == 1
    assert summary.advisory_high_pending == 1
    assert summary.high_pending == 3
    assert summary.needs_human == 1


def test_advisory_high_does_not_block_export():
    from app.schemas import ContractMeta, ReviewResult
    from app.services.jobs import store

    advisory = _finding(
        "a1",
        source=Source.LLM,
        severity=Severity.HIGH,
        status=FindingStatus.PENDING,
        confidence_tier=ConfidenceTier.ADVISORY,
        evidence_quote="原文",
        evidence_start=0,
        evidence_end=2,
        legal_basis_ids=["CC-509"],
    )
    report = ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary([advisory]),
        clauses=[],
        findings=[advisory],
    )
    job = store.create("contract.txt")
    store.set_result(job.job_id, report, [])

    assert store.blocking_high_risk(job.job_id) == []
    assert len(store.advisory_high_risk(job.job_id)) == 1
