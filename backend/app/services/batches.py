"""批次聚合：视图统计、导出门禁。"""

from __future__ import annotations

from collections import Counter

from app.schemas import (
    BatchBlockedItem,
    BatchState,
    BatchStats,
    JobState,
    JobStatus,
    JobSummary,
)
from app.services.jobs import JobNotFound, store
from app.services.summary import blocking_high_findings, build_summary


def load_batch_jobs(batch_id: str) -> list[JobState]:
    summaries = store.list_jobs(batch_id=batch_id, limit=200, order="created_at")
    jobs: list[JobState] = []
    for summary in summaries:
        try:
            jobs.append(store.get(summary.job_id))
        except JobNotFound:
            continue
    return jobs


def _blocked_reason(job: JobState, pending: list) -> str | None:
    if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return "审查尚未完成"
    if job.status == JobStatus.RESUMING:
        return "正在应用复核结果"
    if job.status == JobStatus.AWAITING_REVIEW:
        if pending:
            return f"还有 {len(pending)} 条已核实高风险未复核"
        return "复核结果尚未完成"
    if job.status == JobStatus.FAILED:
        return f"审查失败：{job.error or '未知错误'}"
    if job.status in {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
    } and pending:
        return f"还有 {len(pending)} 条已核实高风险未复核"
    return None


def build_batch_stats(
    summaries: list[JobSummary], jobs: list[JobState]
) -> BatchStats:
    findings = [
        finding
        for job in jobs
        if job.result is not None
        for finding in job.result.findings
    ]
    combined = build_summary(findings)
    by_status = dict(Counter(summary.status.value for summary in summaries))
    by_category: dict[str, dict[str, int]] = {}
    for finding in findings:
        key = finding.category.value
        bucket = by_category.setdefault(
            key, {"total": 0, "high": 0, "medium": 0, "low": 0}
        )
        bucket["total"] += 1
        bucket[finding.severity.value] += 1
    return BatchStats(
        contracts=len(summaries),
        total=combined.total,
        high=combined.high,
        medium=combined.medium,
        low=combined.low,
        pending_review=combined.pending_review,
        blocking_high_pending=combined.blocking_high_pending,
        accepted=combined.accepted,
        rejected=combined.rejected,
        by_status=by_status,
        by_category=by_category,
    )


def load_batch(batch_id: str) -> BatchState | None:
    summaries = store.list_jobs(batch_id=batch_id, limit=200, order="created_at")
    if not summaries:
        return None
    jobs = load_batch_jobs(batch_id)
    jobs_by_id = {job.job_id: job for job in jobs}
    stats = build_batch_stats(summaries, jobs)
    blocked: list[BatchBlockedItem] = []
    for summary in summaries:
        job = jobs_by_id.get(summary.job_id)
        if job is None:
            blocked.append(
                BatchBlockedItem(
                    job_id=summary.job_id,
                    filename=summary.filename,
                    status=summary.status,
                    reason="任务状态不可读",
                )
            )
            continue
        pending = (
            blocking_high_findings(job.result.findings)
            if job.result is not None
            else []
        )
        reason = _blocked_reason(job, pending)
        if reason:
            blocked.append(
                BatchBlockedItem(
                    job_id=summary.job_id,
                    filename=summary.filename,
                    status=job.status,
                    reason=reason,
                    blocking_high_pending=len(pending),
                )
            )
    created = min(summary.created_at for summary in summaries)
    updated = max(summary.updated_at for summary in summaries)
    return BatchState(
        batch_id=batch_id,
        created_at=created,
        updated_at=updated,
        stats=stats,
        export_allowed=not blocked,
        blocked_items=blocked,
        items=summaries,
    )


def batch_export_blockers(batch: BatchState) -> list[BatchBlockedItem]:
    return list(batch.blocked_items)
