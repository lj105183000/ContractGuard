"""报告门禁：证据定位、法条 ID 校验、状态与统计。"""

from __future__ import annotations

import time

from app.graph.evidence import find_quote_span
from app.graph.progress import emit
from app.graph.state import ReviewState
from app.rag.corpus import load_kb
from app.schemas import EventStatus, FindingStatus, Source
from app.services.confidence import TIER_ORDER, apply_confidence_tier
from app.services.summary import build_summary, severity_rank


def gate_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "gate", "证据与引用门禁", EventStatus.STARTED, progress=92)

    report = state.get("report")
    if report is None:
        return {}

    kb = load_kb()
    clauses_by_id = {clause.id: clause for clause in report.clauses}
    warnings = list(report.warnings)

    for finding in report.findings:
        clause = clauses_by_id.get(finding.clause_id or "")
        if finding.clause_id and clause is None:
            finding.clause_id = None
            finding.clause_article = None
            finding.clause_text = None
            finding.gate_notes.append("条款 ID 不存在，已改为合同级风险")

        if clause is not None:
            finding.clause_article = clause.article
            finding.clause_text = clause.text

        quote = finding.evidence_quote.strip()
        if quote:
            span = find_quote_span(clause.text, quote) if clause else None
            if span is None:
                for other in report.clauses:
                    other_span = find_quote_span(other.text, quote)
                    if other_span is not None:
                        finding.clause_id = other.id
                        finding.clause_article = other.article
                        finding.clause_text = other.text
                        clause = other
                        span = other_span
                        break
            if span is None:
                finding.evidence_start = None
                finding.evidence_end = None
                finding.status = FindingStatus.NEEDS_HUMAN
                finding.confidence = min(finding.confidence, 0.3)
                finding.gate_notes.append("证据无法在原文中逐字定位，需人工确认")
            else:
                finding.evidence_start = span[0]
                finding.evidence_end = span[1]
        elif finding.source == Source.LLM:
            finding.status = FindingStatus.NEEDS_HUMAN
            finding.confidence = min(finding.confidence, 0.3)
            finding.gate_notes.append("模型未提供原文证据，需人工确认")
        elif finding.rule_id:
            finding.gate_notes.append("规则基于条款缺失或条件判断，无原文引用")

        valid_ids = [
            law_id for law_id in finding.legal_basis_ids if law_id in kb.legal_by_id
        ]
        rejected = [
            law_id for law_id in finding.legal_basis_ids if law_id not in kb.legal_by_id
        ]
        if rejected:
            finding.rejected_legal_basis_ids = list(
                dict.fromkeys(finding.rejected_legal_basis_ids + rejected)
            )
            finding.gate_notes.append(f"移除不存在的法条引用：{', '.join(rejected)}")
        finding.legal_basis_ids = list(dict.fromkeys(valid_ids))
        if not finding.legal_basis_ids and finding.source in {Source.LLM, Source.RULE_LLM}:
            finding.gate_notes.append("无检索到的法条依据（模型建议）")

        if finding.rule_id and finding.rule_id not in kb.rules_by_id:
            finding.gate_notes.append(f"规则 ID 不存在：{finding.rule_id}")
            finding.rule_id = None
        if finding.rule_hint and finding.rule_hint not in kb.rules_by_id:
            finding.gate_notes.append(f"规则提示不存在：{finding.rule_hint}")
            finding.rule_hint = None
        finding.gate_notes = list(dict.fromkeys(finding.gate_notes))

    if not report.findings:
        warnings.append(
            "未发现规则/模型可判定的风险；这不代表合同没有风险，请人工通读全文。"
        )

    clause_order = {clause.id: clause.index for clause in report.clauses}
    for finding in report.findings:
        apply_confidence_tier(finding)
    report.findings.sort(
        key=lambda item: (
            severity_rank(item.severity),
            clause_order.get(item.clause_id or "", 999),
            TIER_ORDER.get(item.confidence_tier, 9),
        )
    )

    report.summary = build_summary(report.findings)
    report.warnings = list(dict.fromkeys(warnings))

    legal_basis = {}
    playbook = {}
    for finding in report.findings:
        for law_id in finding.legal_basis_ids:
            article = kb.legal_by_id.get(law_id)
            if article:
                legal_basis[law_id] = article
        for rule_id in (finding.rule_id, finding.rule_hint):
            if rule_id and rule_id in kb.rules_by_id:
                playbook[rule_id] = kb.rules_by_id[rule_id]
    report.legal_basis = legal_basis
    report.playbook = playbook

    evidence_text = (
        f"{report.summary.evidence_valid_rate:.0%}"
        if report.summary.evidence_valid_rate is not None
        else "—"
    )
    emit(
        job_id,
        "gate",
        "证据与引用门禁",
        EventStatus.COMPLETED,
        message=(
            f"{len(report.findings)} 条风险；待复核 {report.summary.pending_review} 条；"
            f"证据定位率 {evidence_text}"
        ),
        progress=96,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"report": report}
