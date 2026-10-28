"""确定性置信分层。

只使用可验证信号（来源、证据是否定位、法条 ID 是否有效），
不使用模型自报的 ``Finding.confidence`` 数值，避免把未校准的模型自评分
当成可信度。
"""

from __future__ import annotations

from app.schemas import ConfidenceTier, Finding, FindingStatus, Source

TIER_ORDER = {
    ConfidenceTier.CONFIRMED: 0,
    ConfidenceTier.ADVISORY: 1,
    ConfidenceTier.NEEDS_REVIEW: 2,
}


def classify_finding(finding: Finding) -> tuple[ConfidenceTier, list[str]]:
    """返回 (tier, 判定理由)，理由只描述确定性信号。"""
    reasons: list[str] = []

    if finding.source in {Source.RULE, Source.RULE_LLM}:
        if finding.rule_id:
            reasons.append(f"规则引擎命中 {finding.rule_id}")
        else:
            reasons.append("规则引擎命中")
        if finding.source == Source.RULE_LLM:
            reasons.append("规则与模型结论已合并")
        if finding.evidence_quote.strip() and finding.evidence_start is None:
            reasons.append("规则证据无法在原文中定位")
            return ConfidenceTier.NEEDS_REVIEW, reasons
        return ConfidenceTier.CONFIRMED, reasons

    if finding.source == Source.LLM:
        has_evidence = (
            bool(finding.evidence_quote.strip())
            and finding.evidence_start is not None
            and finding.evidence_end is not None
            and finding.evidence_end >= finding.evidence_start
        )
        has_law = bool(finding.legal_basis_ids)
        reasons.append(
            "原文证据已逐字定位" if has_evidence else "未提供可定位的原文证据"
        )
        reasons.append(
            "法条引用通过 ID 校验" if has_law else "没有可用的法条依据"
        )
        if has_evidence and has_law:
            reasons.append("模型单独提出，未获规则引擎佐证")
            return ConfidenceTier.ADVISORY, reasons
        return ConfidenceTier.NEEDS_REVIEW, reasons

    reasons.append("未知来源，无法核实")
    return ConfidenceTier.NEEDS_REVIEW, reasons


def apply_confidence_tier(
    finding: Finding, *, mark_needs_human: bool = True
) -> Finding:
    """写入 tier 与理由；待核实项默认转为 needs_human。"""
    tier, reasons = classify_finding(finding)
    finding.confidence_tier = tier
    finding.confidence_reasons = list(dict.fromkeys(reasons))
    if (
        mark_needs_human
        and tier == ConfidenceTier.NEEDS_REVIEW
        and finding.status == FindingStatus.PENDING
    ):
        finding.status = FindingStatus.NEEDS_HUMAN
        finding.confidence = min(finding.confidence, 0.3)
        finding.confidence_reasons.append("确定性信号不足，已转为人工确认")
    return finding
