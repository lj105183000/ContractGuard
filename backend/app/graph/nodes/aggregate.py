"""聚合节点：合并规则与三个 Agent 的结果，去重、定位、排序。"""

from __future__ import annotations

import re
import time

from app.config import DISCLAIMER, settings
from app.graph.progress import emit
from app.graph.state import ReviewState
from app.llm.profiles import chat_runtime
from app.rag.corpus import load_kb
from app.schemas import (
    ContractMeta,
    EventStatus,
    Finding,
    FindingStatus,
    ReviewResult,
    Source,
)
from app.services.summary import build_summary, severity_rank


def _dedupe_key(finding: Finding) -> str:
    if finding.rule_id:
        return f"rule:{finding.rule_id}:{finding.clause_id or 'contract'}"
    if finding.rule_hint:
        return f"rule:{finding.rule_hint}:{finding.clause_id or 'contract'}"
    quote = re.sub(r"\s+", "", finding.evidence_quote)[:30]
    return f"llm:{finding.clause_id or 'contract'}:{finding.category.value}:{quote}"


def _normalized_quote(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _quote_similar(left: str, right: str) -> bool:
    left_text, right_text = _normalized_quote(left), _normalized_quote(right)
    if not left_text or not right_text:
        return False
    if left_text in right_text or right_text in left_text:
        return True
    left_bigrams, right_bigrams = _bigrams(left_text), _bigrams(right_text)
    if not left_bigrams or not right_bigrams:
        return False
    overlap = len(left_bigrams & right_bigrams)
    union = len(left_bigrams | right_bigrams)
    return union > 0 and overlap / union >= 0.6


def _span_overlap(left: Finding, right: Finding) -> bool:
    if (
        left.evidence_start is None
        or left.evidence_end is None
        or right.evidence_start is None
        or right.evidence_end is None
    ):
        return False
    start = max(left.evidence_start, right.evidence_start)
    end = min(left.evidence_end, right.evidence_end)
    if end <= start:
        return False
    shorter = min(
        left.evidence_end - left.evidence_start,
        right.evidence_end - right.evidence_start,
    )
    return shorter > 0 and (end - start) / shorter >= 0.5


def _similar_findings(
    left: Finding, right: Finding, *, allow_title: bool = True
) -> bool:
    """同一风险的第二层判定：同条款、同类别，且证据/标题高度相似。"""
    if left.clause_id != right.clause_id or left.category != right.category:
        return False
    if _quote_similar(left.evidence_quote, right.evidence_quote):
        return True
    if _span_overlap(left, right):
        return True
    if not allow_title:
        return False
    left_title = re.sub(r"\s+", "", left.title or "")
    right_title = re.sub(r"\s+", "", right.title or "")
    return bool(left_title) and left_title == right_title


def _is_rule_anchored(finding: Finding) -> bool:
    return finding.source in {Source.RULE, Source.RULE_LLM} or bool(finding.rule_id)


def _merge_into(existing: Finding, incoming: Finding, reason: str) -> None:
    """把 incoming 合并进 existing，保留 existing 的规则锚点。"""
    sources = {existing.source, incoming.source}
    if Source.RULE in sources and Source.LLM in sources:
        existing.source = Source.RULE_LLM
    elif Source.RULE_LLM in sources:
        existing.source = Source.RULE_LLM
    if severity_rank(incoming.severity) < severity_rank(existing.severity):
        existing.severity = incoming.severity
    existing.confidence = max(existing.confidence, incoming.confidence)
    existing.legal_basis_ids = list(
        dict.fromkeys(existing.legal_basis_ids + incoming.legal_basis_ids)
    )
    existing.rejected_legal_basis_ids = list(
        dict.fromkeys(
            existing.rejected_legal_basis_ids + incoming.rejected_legal_basis_ids
        )
    )
    existing.rule_id = existing.rule_id or incoming.rule_id
    existing.rule_hint = existing.rule_hint or incoming.rule_hint
    existing.recommendation = existing.recommendation or incoming.recommendation
    existing.fallback = existing.fallback or incoming.fallback
    existing.agent = existing.agent or incoming.agent
    if incoming.analysis and incoming.analysis not in existing.analysis:
        existing.analysis = (
            existing.analysis + "\n\n模型补充：" + incoming.analysis
        ).strip()
    if existing.evidence_start is None and incoming.evidence_start is not None:
        existing.clause_id = incoming.clause_id
        existing.clause_article = incoming.clause_article
        existing.clause_text = incoming.clause_text
        existing.evidence_quote = incoming.evidence_quote
        existing.evidence_start = incoming.evidence_start
        existing.evidence_end = incoming.evidence_end
    if (
        existing.status == FindingStatus.NEEDS_HUMAN
        and incoming.status == FindingStatus.PENDING
    ):
        existing.status = FindingStatus.PENDING
    notes = list(existing.gate_notes) + list(incoming.gate_notes)
    if reason:
        notes.append(reason)
    existing.gate_notes = list(dict.fromkeys(notes))


def _merge_similar(findings: list[Finding]) -> list[Finding]:
    """第二遍保守合并：只处理同条款、同类别且证据高度重叠的风险。"""
    groups: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        key = (finding.clause_id or "contract", finding.category.value)
        groups.setdefault(key, []).append(finding)

    order = {id(finding): index for index, finding in enumerate(findings)}
    merged: list[Finding] = []
    for items in groups.values():
        anchors = [item for item in items if _is_rule_anchored(item)]
        llm_only = [item for item in items if not _is_rule_anchored(item)]

        remaining: list[Finding] = []
        for item in llm_only:
            target = next(
                (
                    anchor
                    for anchor in anchors
                    if _similar_findings(anchor, item, allow_title=False)
                ),
                None,
            )
            if target is None:
                remaining.append(item)
                continue
            _merge_into(
                target,
                item,
                "与规则引擎命中项合并（同条款、同类、证据重叠）",
            )

        kept: list[Finding] = []
        for item in remaining:
            target = next(
                (
                    candidate
                    for candidate in kept
                    if _similar_findings(candidate, item)
                ),
                None,
            )
            if target is None:
                kept.append(item)
                continue
            _merge_into(
                target,
                item,
                "与同类模型建议合并（证据高度重叠）",
            )

        merged.extend(anchors + kept)

    merged.sort(key=lambda finding: order.get(id(finding), 999))
    return merged


def _merge_findings(findings: list[Finding]) -> list[Finding]:
    merged: dict[str, Finding] = {}
    for finding in findings:
        key = _dedupe_key(finding)
        existing = merged.get(key)
        if existing is None:
            merged[key] = finding.model_copy(deep=True)
            continue

        sources = {existing.source, finding.source}
        if Source.RULE in sources and Source.LLM in sources:
            existing.source = Source.RULE_LLM
            existing.gate_notes = list(
                dict.fromkeys(existing.gate_notes + ["规则与模型结论合并"])
            )
        elif Source.RULE_LLM in sources:
            existing.source = Source.RULE_LLM
        if severity_rank(finding.severity) < severity_rank(existing.severity):
            existing.severity = finding.severity
        existing.confidence = max(existing.confidence, finding.confidence)
        existing.legal_basis_ids = list(
            dict.fromkeys(existing.legal_basis_ids + finding.legal_basis_ids)
        )
        existing.rejected_legal_basis_ids = list(
            dict.fromkeys(
                existing.rejected_legal_basis_ids
                + finding.rejected_legal_basis_ids
            )
        )
        existing.rule_id = existing.rule_id or finding.rule_id
        existing.rule_hint = existing.rule_hint or finding.rule_hint
        existing.recommendation = existing.recommendation or finding.recommendation
        existing.fallback = existing.fallback or finding.fallback
        existing.agent = existing.agent or finding.agent
        if finding.analysis and finding.analysis not in existing.analysis:
            existing.analysis = (
                existing.analysis + "\n\n模型补充：" + finding.analysis
            ).strip()
        if existing.evidence_start is None and finding.evidence_start is not None:
            existing.clause_id = finding.clause_id
            existing.clause_article = finding.clause_article
            existing.clause_text = finding.clause_text
            existing.evidence_quote = finding.evidence_quote
            existing.evidence_start = finding.evidence_start
            existing.evidence_end = finding.evidence_end
        if existing.status == FindingStatus.NEEDS_HUMAN and finding.status == FindingStatus.PENDING:
            existing.status = FindingStatus.PENDING
        existing.gate_notes = list(
            dict.fromkeys(existing.gate_notes + finding.gate_notes)
        )
    return _merge_similar(list(merged.values()))


def aggregate_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "aggregate", "合并与去重", EventStatus.STARTED, progress=72)

    kb = load_kb()
    clauses = state.get("clauses", [])
    clauses_by_id = {clause.id: clause for clause in clauses}
    clause_order = {clause.id: clause.index for clause in clauses}
    findings = _merge_findings(state.get("findings", []))

    warnings: list[str] = list(state.get("warnings", []))
    for finding in findings:
        if finding.clause_id and finding.clause_id in clauses_by_id:
            clause = clauses_by_id[finding.clause_id]
            finding.clause_article = clause.article
            finding.clause_text = clause.text
        elif finding.clause_id:
            warnings.append(f"风险 {finding.id} 指向不存在的条款，已改为合同级")
            finding.clause_id = None
            finding.clause_article = None
            finding.clause_text = None
            if finding.evidence_start is not None:
                finding.evidence_start = None
                finding.evidence_end = None

    for finding in findings:
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

    findings.sort(
        key=lambda item: (
            severity_rank(item.severity),
            clause_order.get(item.clause_id or "", 999),
            -item.confidence,
        )
    )

    legal_basis = {}
    playbook = {}
    for finding in findings:
        for law_id in finding.legal_basis_ids:
            article = kb.legal_by_id.get(law_id)
            if article:
                legal_basis[law_id] = article
        for rule_id in (finding.rule_id, finding.rule_hint):
            if rule_id and rule_id in kb.rules_by_id:
                playbook[rule_id] = kb.rules_by_id[rule_id]

    report = ReviewResult(
        contract=ContractMeta(
            filename=state.get("filename", ""),
            source_type=state.get("source_type", "txt"),
            title=state.get("title"),
            page_count=state.get("page_count", 0),
            clause_count=len(clauses),
        ),
        summary=build_summary(findings),
        clauses=clauses,
        findings=findings,
        legal_basis=legal_basis,
        playbook=playbook,
        full_text=state.get("contract_text", ""),
        warnings=list(dict.fromkeys(warnings)),
        mode=state.get("llm_mode") or chat_runtime(settings).mode,
        disclaimer=DISCLAIMER,
    )

    emit(
        job_id,
        "aggregate",
        "合并与去重",
        EventStatus.COMPLETED,
        message=f"合并后 {len(findings)} 条风险",
        progress=75,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"report": report}
