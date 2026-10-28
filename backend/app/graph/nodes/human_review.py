"""图内人工复核节点：暂停等待决策，resume 后应用决策。"""

from __future__ import annotations

from langgraph.types import interrupt

from app.graph.state import ReviewState
from app.schemas import Decision, FindingStatus
from app.services.summary import blocking_high_findings, build_summary


def _apply_decisions(report, decisions: dict) -> int:
    applied = 0
    for finding in report.findings:
        decision = decisions.get(finding.id)
        if decision is None:
            continue
        if isinstance(decision, dict):
            value = decision.get("decision")
            note = decision.get("note", "")
        else:
            value = decision
            note = ""
        if value not in {Decision.ACCEPTED.value, Decision.REJECTED.value}:
            continue
        finding.status = (
            FindingStatus.ACCEPTED
            if value == Decision.ACCEPTED.value
            else FindingStatus.REJECTED
        )
        finding.review_note = note or ""
        applied += 1
    return applied


async def human_review_node(state: ReviewState) -> dict:
    """在 gate 之后暂停；没有待处理的已核实高风险时直接放行。"""
    report = state.get("report")
    if report is None:
        return {}

    while True:
        pending = blocking_high_findings(report.findings)
        if not pending:
            report.summary = build_summary(report.findings)
            return {"report": report}

        # interrupt 之前的逻辑会在 resume 时从头重跑，因此这里只做纯计算，
        # 不发送进度事件、不写文件、不调用 LLM。
        resume_value = interrupt(
            {
                "type": "review_required",
                "job_id": state.get("job_id"),
                "pending_finding_ids": [finding.id for finding in pending],
            }
        )
        decisions = {}
        if isinstance(resume_value, dict):
            decisions = resume_value.get("decisions") or {}
        applied = _apply_decisions(report, decisions)
        report.summary = build_summary(report.findings)
        if applied == 0:
            raise RuntimeError("人工复核恢复值中没有可应用的决策。")
