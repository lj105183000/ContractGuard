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
import json

import pytest

from app.services.jobs import JobNotDeletable, JobNotFound, JobStore
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


def test_delete_completed_job_removes_snapshot_and_source(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    source = tmp_path / "uploads" / "contract.txt"
    source.parent.mkdir()
    source.write_text("合同正文", encoding="utf-8")
    store = JobStore(db_path)
    try:
        job = store.create(
            "contract.txt", source_path=str(source), cleanup_source=True
        )
        store.set_running(job.job_id)
        store.set_result(job.job_id, _report(), [])
        store.delete(job.job_id)
        assert not source.exists()
        assert store.list_jobs() == []
        with pytest.raises(JobNotFound):
            store.get(job.job_id)
    finally:
        store.close()


def test_delete_removes_feedback_and_run_trace(tmp_path, monkeypatch):
    feedback_dir = tmp_path / "feedback"
    runs_dir = tmp_path / "runs"
    feedback_dir.mkdir()
    runs_dir.mkdir()
    monkeypatch.setattr("app.services.jobs.FEEDBACK_DIR", feedback_dir)
    monkeypatch.setattr("app.services.jobs.RUNS_DIR", runs_dir)
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = store.create("contract.txt")
        store.set_failed(job.job_id, "boom")
        feedback = feedback_dir / "feedback.jsonl"
        feedback.write_text(
            json.dumps({"job_id": job.job_id})
            + "\n"
            + json.dumps({"job_id": "other-job"})
            + "\n",
            encoding="utf-8",
        )
        (runs_dir / f"{job.job_id}.json").write_text("{}", encoding="utf-8")

        store.delete(job.job_id)

        assert not (runs_dir / f"{job.job_id}.json").exists()
        remaining = feedback.read_text(encoding="utf-8")
        assert job.job_id not in remaining
        assert "other-job" in remaining
    finally:
        store.close()


def test_delete_active_job_is_rejected(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = store.create("contract.txt")
        with pytest.raises(JobNotDeletable):
            store.delete(job.job_id)
        assert store.get(job.job_id).status == JobStatus.QUEUED
    finally:
        store.close()


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
