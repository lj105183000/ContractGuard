"""三个专业化 LLM 审查 Agent。

每个 Agent：
1. 只取自己负责的条款，构造领域查询；
2. 分别检索法条和 Playbook（BM25 + 向量 + RRF）；
3. 调用 LLM 输出结构化 findings；
4. 在节点内做第一层证据/引用校验，交给聚合与门禁做第二层。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.graph.evidence import find_quote_span
from app.graph.progress import emit
from app.graph.state import ReviewState
from app.llm.client import get_llm
from app.llm.prompts import AGENTS, AGENT_SYSTEM, AgentSpec, build_agent_prompt
from app.rag.corpus import KnowledgeBase, load_kb
from app.rag.retriever import get_retriever
from app.schemas import (
    AgentOutput,
    Clause,
    ContractFacts,
    EventStatus,
    Finding,
    FindingStatus,
    LLMFinding,
    Source,
)


def _select_clauses(spec: AgentSpec, clauses: list[Clause]) -> list[Clause]:
    focused = [clause for clause in clauses if clause.clause_type in spec.focus_clause_types]
    if not focused:
        focused = list(clauses)
    # 条款多时优先保留靠前的条款，避免 prompt 过长
    return focused[:10]


def _build_query(spec: AgentSpec, clauses: list[Clause], facts: ContractFacts) -> str:
    parts: list[str] = [spec.instruction, " ".join(spec.focus_tags)]
    parts.extend(clause.text[:300] for clause in clauses)
    payment = facts.payment
    if payment.prepayment_ratio is not None:
        parts.append(f"预付款比例 {payment.prepayment_ratio:.0%}")
    if payment.payment_days is not None:
        parts.append(f"付款期限 {payment.payment_days} 天")
    if payment.retention_ratio is not None:
        parts.append(f"质保金 {payment.retention_ratio:.0%}")
    if facts.warranty.months is not None:
        parts.append(f"质保期 {facts.warranty.months} 个月")
    if facts.liability.penalty_daily_rate is not None:
        parts.append(f"违约金日费率 {facts.liability.penalty_daily_rate}")
    if facts.dispute.court:
        parts.append(f"管辖 {facts.dispute.court}")
    if facts.dispute.arbitration:
        parts.append(f"仲裁 {facts.dispute.arbitration}")
    return " ".join(parts)[:4000]


def _law_payload(kb: KnowledgeBase, ids: list[str]) -> list[dict[str, Any]]:
    payload = []
    for law_id in ids:
        article = kb.legal_by_id.get(law_id)
        if not article:
            continue
        payload.append(
            {
                "id": article.id,
                "law": article.law,
                "article": article.article,
                "title": article.title,
                "text": article.text[:900],
            }
        )
    return payload


def _rule_payload(kb: KnowledgeBase, ids: list[str]) -> list[dict[str, Any]]:
    payload = []
    for rule_id in ids:
        rule = kb.rules_by_id.get(rule_id)
        if not rule:
            continue
        payload.append(
            {
                "id": rule.id,
                "title": rule.title,
                "description": rule.description,
                "recommendation": rule.recommendation,
                "fallback": rule.fallback,
            }
        )
    return payload


def _finding_id(spec: AgentSpec, clause: Clause, item: LLMFinding) -> str:
    seed = f"{clause.id}|{item.title}|{item.evidence_quote[:120]}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"llm-{spec.key}-{clause.id}-{digest}"


def _validate_findings(
    spec: AgentSpec,
    items: list[LLMFinding],
    clauses: list[Clause],
    allowed_law_ids: set[str],
    kb: KnowledgeBase,
) -> tuple[list[Finding], list[str]]:
    clauses_by_id = {clause.id: clause for clause in clauses}
    findings: list[Finding] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for item in items[:4]:
        clause = clauses_by_id.get(item.clause_id)
        if clause is None:
            warnings.append(f"{spec.name} 返回了未知条款 ID：{item.clause_id}，已丢弃")
            continue

        quote = item.evidence_quote.strip()
        span = find_quote_span(clause.text, quote)
        if span is None and quote:
            # 模型可能把条款 ID 标错；在其他条款里再找一次原文
            for other in clauses:
                other_span = find_quote_span(other.text, quote)
                if other_span is not None:
                    clause = other
                    span = other_span
                    break

        notes: list[str] = []
        rejected = [law_id for law_id in item.legal_basis_ids if law_id not in allowed_law_ids]
        legal_ids = [law_id for law_id in item.legal_basis_ids if law_id in allowed_law_ids]
        if rejected:
            notes.append(f"移除了不在检索上下文中的法条引用：{', '.join(rejected)}")
        if not legal_ids:
            notes.append("无检索到的法条依据（模型建议）")

        rule = kb.rules_by_id.get(item.rule_hint) if item.rule_hint else None
        if item.rule_hint and not rule:
            notes.append(f"rule_hint 不存在：{item.rule_hint}")

        status = FindingStatus.PENDING
        confidence = item.confidence
        if span is None:
            status = FindingStatus.NEEDS_HUMAN
            confidence = min(confidence, 0.3)
            notes.append("证据无法在原文中逐字定位，需人工确认")

        title = item.title.strip() or (rule.title if rule else "模型识别风险")
        finding = Finding(
            id=_finding_id(spec, clause, item),
            clause_id=clause.id,
            clause_article=clause.article,
            clause_text=clause.text,
            category=item.category,
            severity=item.severity,
            source=Source.LLM,
            title=title,
            analysis=item.analysis or (rule.description if rule else ""),
            evidence_quote=quote,
            evidence_start=span[0] if span else None,
            evidence_end=span[1] if span else None,
            legal_basis_ids=legal_ids,
            rejected_legal_basis_ids=rejected,
            rule_hint=rule.id if rule else None,
            agent=spec.key,
            confidence=confidence,
            recommendation=rule.recommendation if rule else "",
            fallback=rule.fallback if rule else "",
            status=status,
            gate_notes=notes,
        )
        if finding.id in seen:
            continue
        seen.add(finding.id)
        findings.append(finding)
    return findings, warnings


def make_agent_node(spec: AgentSpec):  # type: ignore[no-untyped-def]
    async def node(state: ReviewState) -> dict:
        job_id = state.get("job_id", "unknown")
        started = time.perf_counter()
        emit(job_id, f"agent_{spec.key}", spec.name, EventStatus.STARTED, progress=32)

        clauses = _select_clauses(spec, state.get("clauses", []))
        facts = state["facts"]
        kb = load_kb()
        retriever = get_retriever()
        query = _build_query(spec, clauses, facts)

        law_results = await retriever.retrieve(query, top_k=6, doc_types=["law"])
        rule_results = await retriever.retrieve(
            query,
            top_k=4,
            doc_types=["rule"],
            categories=list(spec.focus_categories),
        )
        law_payload = _law_payload(kb, [item.document.id for item in law_results])
        rule_payload = _rule_payload(kb, [item.document.id for item in rule_results])
        allowed_law_ids = {item["id"] for item in law_payload}

        try:
            llm = get_llm(
                state["trace"], f"agent_{spec.key}", state.get("llm_route")
            )
            output = await llm.chat_json(
                f"agent_{spec.key}",
                AGENT_SYSTEM,
                build_agent_prompt(spec, clauses, facts, law_payload, rule_payload),
                AgentOutput,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"{spec.name} 调用失败：{exc}"
            emit(
                job_id,
                f"agent_{spec.key}",
                spec.name,
                EventStatus.FAILED,
                message=message,
                progress=70,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return {"warnings": [message]}

        findings, warnings = _validate_findings(
            spec, output.findings, state.get("clauses", []), allowed_law_ids, kb
        )
        message = f"{len(findings)} 条风险 · 检索模式 {retriever.retrieval_mode}"
        if retriever.last_warning:
            warnings.append(retriever.last_warning)
        emit(
            job_id,
            f"agent_{spec.key}",
            spec.name,
            EventStatus.COMPLETED,
            message=message,
            progress=70,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {"findings": findings, "warnings": warnings}

    return node


AGENT_NODES = {
    f"agent_{spec.key}": make_agent_node(spec) for spec in AGENTS
}
