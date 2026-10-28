from app.schemas import (
    Category,
    ContractMeta,
    Decision,
    Finding,
    FindingStatus,
    JobStatus,
    ReviewResult,
    Severity,
    Source,
)
from app.services.jobs import JobStore
from app.services.summary import build_summary


def _report(finding: Finding | None = None) -> ReviewResult:
    findings = [finding] if finding else []
    return ReviewResult(
        contract=ContractMeta(filename="contract.txt"),
        summary=build_summary(findings),
        clauses=[],
        findings=findings,
    )


def _finding() -> Finding:
    return Finding(
        id="f1",
        category=Category.PAYMENT,
        severity=Severity.HIGH,
        source=Source.RULE,
        title="预付款比例过高",
    )


def test_completed_job_survives_new_store_instance(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    job = first.create("contract.txt", "balanced")
    first.set_running(job.job_id)
    first.set_result(job.job_id, _report(), [])
    first.close()

    second = JobStore(db_path)
    try:
        restored = second.get(job.job_id)
        assert restored.status == JobStatus.COMPLETED
        assert restored.result is not None
        assert restored.filename == "contract.txt"
    finally:
        second.close()


def test_list_jobs_returns_summary_without_full_state(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    try:
        job = store.create("contract.txt")
        store.set_result(job.job_id, _report(_finding()), [])
        summaries = store.list_jobs()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.job_id == job.job_id
        assert summary.summary is not None
        assert summary.summary.total == 1
        assert summary.summary.high == 1
        assert not hasattr(summary, "result")
    finally:
        store.close()


def test_recover_incomplete_marks_running_as_failed(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    job = first.create("contract.txt")
    first.set_running(job.job_id)
    first.close()

    second = JobStore(db_path)
    try:
        restored = second.get(job.job_id)
        assert restored.status == JobStatus.FAILED
        assert "中断" in (restored.error or "")
    finally:
        second.close()


def test_decision_is_persisted(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    first = JobStore(db_path)
    finding = _finding()
    job = first.create("contract.txt")
    first.set_result(job.job_id, _report(finding), [])
    first.decide(job.job_id, finding.id, Decision.ACCEPTED, "同意")
    first.close()

    second = JobStore(db_path)
    try:
        restored = second.get(job.job_id)
        assert restored.result is not None
        assert restored.result.findings[0].status == FindingStatus.ACCEPTED
        assert restored.result.findings[0].review_note == "同意"
        assert restored.result.findings[0].reviewed_at
        assert restored.result.findings[0].review_history[-1].decision == (
            Decision.ACCEPTED
        )
    finally:
        second.close()


def test_reset_clears_memory_and_database(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    try:
        job = store.create("contract.txt")
        store.reset()
        assert store.list_jobs() == []
        assert store._jobs == {}
        assert job.job_id not in store._jobs
    finally:
        store.close()
