"""风险统计与指标口径。

指标集中在这里计算，避免 aggregate / gate / API / eval 各写一套口径。
"""

from __future__ import annotations

from app.schemas import (
    ConfidenceTier,
    Finding,
    FindingStatus,
    ReviewSummary,
    Severity,
    Source,
)

SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}

TIER_RANK = {
    ConfidenceTier.CONFIRMED: 0,
    ConfidenceTier.ADVISORY: 1,
    ConfidenceTier.NEEDS_REVIEW: 2,
}


def severity_rank(severity: Severity) -> int:
    return SEVERITY_RANK.get(severity, 9)


def blocking_high_findings(findings: list[Finding]) -> list[Finding]:
    """已核实、尚未处理的高风险；只有这些会触发图内人工复核与导出阻断。"""
    return [
        finding
        for finding in findings
        if finding.severity == Severity.HIGH
        and finding.confidence_tier == ConfidenceTier.CONFIRMED
        and finding.status in {FindingStatus.PENDING, FindingStatus.NEEDS_HUMAN}
    ]


def compute_evidence_metrics(
    findings: list[Finding],
) -> tuple[int, int, float | None]:
    """证据定位率。

    只有提供了 evidence_quote 的风险才进入分母；规则命中但无原文引用的
    （例如条款缺失类规则）不计入，避免把“没有引用”算成“定位成功”。
    """
    checked = [finding for finding in findings if finding.evidence_quote.strip()]
    valid = [
        finding
        for finding in checked
        if finding.evidence_start is not None
        and finding.evidence_end is not None
        and finding.evidence_end >= finding.evidence_start
    ]
    rate = len(valid) / len(checked) if checked else None
    return len(checked), len(valid), rate


def compute_citation_metrics(
    findings: list[Finding],
) -> tuple[int, int, float | None]:
    """法条 ID 可解析率。

    分母包含被移除的无效 ID，衡量的是模型原始引用中能对上知识库的比例；
    不衡量法条与风险的语义相关性。
    """
    valid = sum(len(finding.legal_basis_ids) for finding in findings)
    rejected = sum(len(finding.rejected_legal_basis_ids) for finding in findings)
    checked = valid + rejected
    rate = valid / checked if checked else None
    return checked, valid, rate


def build_summary(
    findings: list[Finding],
    evidence_valid_rate: float | None = None,
    citation_valid_rate: float | None = None,
) -> ReviewSummary:
    counts = {Severity.HIGH: 0, Severity.MEDIUM: 0, Severity.LOW: 0}
    accepted = rejected = needs_human = pending = high_pending = 0
    blocking_high_pending = advisory_high_pending = 0
    by_source: dict[str, int] = {}
    by_confidence: dict[str, int] = {}

    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
        by_source[finding.source.value] = by_source.get(finding.source.value, 0) + 1
        tier_key = (
            finding.confidence_tier.value
            if finding.confidence_tier is not None
            else "unknown"
        )
        by_confidence[tier_key] = by_confidence.get(tier_key, 0) + 1

        if finding.status == FindingStatus.ACCEPTED:
            accepted += 1
        elif finding.status == FindingStatus.REJECTED:
            rejected += 1
        else:
            pending += 1
            if finding.status == FindingStatus.NEEDS_HUMAN:
                needs_human += 1
            if finding.severity == Severity.HIGH:
                high_pending += 1
                if finding.confidence_tier == ConfidenceTier.CONFIRMED:
                    blocking_high_pending += 1
                elif finding.confidence_tier == ConfidenceTier.ADVISORY:
                    advisory_high_pending += 1

    evidence_checked, _evidence_valid, computed_evidence_rate = (
        compute_evidence_metrics(findings)
    )
    citation_checked, _citation_valid, computed_citation_rate = (
        compute_citation_metrics(findings)
    )

    return ReviewSummary(
        total=len(findings),
        high=counts.get(Severity.HIGH, 0),
        medium=counts.get(Severity.MEDIUM, 0),
        low=counts.get(Severity.LOW, 0),
        pending_review=pending,
        high_pending=high_pending,
        accepted=accepted,
        rejected=rejected,
        needs_human=needs_human,
        evidence_valid_rate=(
            evidence_valid_rate
            if evidence_valid_rate is not None
            else computed_evidence_rate
        ),
        citation_valid_rate=(
            citation_valid_rate
            if citation_valid_rate is not None
            else computed_citation_rate
        ),
        by_source=by_source,
        by_confidence=by_confidence,
        blocking_high_pending=blocking_high_pending,
        advisory_high_pending=advisory_high_pending,
        evidence_checked=evidence_checked,
        citation_checked=citation_checked,
    )
