"""修改建议节点：LLM 生成可直接替换的条款，失败时用 Playbook 兜底。"""

from __future__ import annotations

import time

from app.graph.progress import emit
from app.graph.state import ReviewState
from app.llm.client import get_llm
from app.llm.prompts import REDLINE_SYSTEM, build_redline_prompt
from app.schemas import EventStatus, Finding, RedlineOutput

BATCH_SIZE = 8


def _fallback_text(finding: Finding) -> str:
    if finding.recommendation:
        return finding.recommendation
    return f"建议补充明确约定：{finding.title}。具体条款需结合交易背景由业务与法务确认。"


def _apply_fallback(finding: Finding) -> None:
    if not finding.suggested_text:
        finding.suggested_text = _fallback_text(finding)
    if not finding.fallback:
        finding.fallback = "具体底线需结合交易金额、供应商可替代性和业务紧迫度确认。"
    if not finding.negotiation_note:
        finding.negotiation_note = "优先用条款措辞解决；对方坚持时再评估商业让步。"


async def redline_node(state: ReviewState) -> dict:
    job_id = state.get("job_id", "unknown")
    started = time.perf_counter()
    emit(job_id, "redline", "生成修改建议", EventStatus.STARTED, progress=82)

    report = state.get("report")
    if report is None or not report.findings:
        emit(
            job_id,
            "redline",
            "生成修改建议",
            EventStatus.SKIPPED,
            message="没有风险，跳过修改建议",
            progress=88,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {"report": report}

    clauses_by_id = {clause.id: clause for clause in state.get("clauses", [])}
    fallback_count = 0
    warnings: list[str] = []
    llm = None
    try:
        llm = get_llm(state["trace"], "redline", state.get("llm_route"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"修改建议模型不可用，使用模板兜底：{exc}")

    for start in range(0, len(report.findings), BATCH_SIZE):
        batch = report.findings[start : start + BATCH_SIZE]
        items_by_id = {}
        if llm is not None:
            try:
                output = await llm.chat_json(
                    "redline",
                    REDLINE_SYSTEM,
                    build_redline_prompt(batch, clauses_by_id),
                    RedlineOutput,
                )
                items_by_id = {item.finding_id: item for item in output.redlines}
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"修改建议生成失败，本批使用模板兜底：{exc}")

        for finding in batch:
            item = items_by_id.get(finding.id)
            llm_text = item.suggested_text.strip() if item else ""
            if llm_text:
                finding.suggested_text = llm_text
                finding.suggested_text_source = "llm"
            else:
                finding.suggested_text_source = "template"
            if item:
                finding.fallback = item.fallback.strip() or finding.fallback
                finding.negotiation_note = item.negotiation_note.strip()
            if not finding.suggested_text:
                fallback_count += 1
            _apply_fallback(finding)

    if warnings:
        report.warnings = list(dict.fromkeys(report.warnings + warnings))
    emit(
        job_id,
        "redline",
        "生成修改建议",
        EventStatus.COMPLETED,
        message=f"{len(report.findings)} 条建议；模板兜底 {fallback_count} 条",
        progress=88,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return {"report": report}
