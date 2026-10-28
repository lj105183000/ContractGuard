import pytest

from app.schemas import ContractMeta, JobStatus, ReviewResult
from app.services.jobs import (
    JobNotCancellable,
    JobNotRetryable,
    JobStore,
    SourceMissing,
)
from app.services.summary import build_summary


def test_cancel_queued_job_is_immediate(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = store.create("contract.txt")
        store.mark_ready(job.job_id)

        cancelled = store.request_cancel(job.job_id)

        assert cancelled.status == JobStatus.CANCELLED
        assert store.get(job.job_id, fresh=True).status == JobStatus.CANCELLED
        with pytest.raises(JobNotCancellable):
            store.request_cancel(job.job_id)
    finally:
        store.close()


def test_cancel_running_job_then_retry_keeps_source(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    source = tmp_path / "contract.txt"
    source.write_text("设备采购合同", encoding="utf-8")
    store = JobStore(db_path)
    try:
        job = store.create(
            "contract.txt", source_path=str(source), cleanup_source=False
        )
        store.mark_ready(job.job_id)
        claim = store.claim("worker-1", 60)
        assert claim is not None

        requested = store.request_cancel(job.job_id)
        assert requested.cancel_requested is True
        assert store.heartbeat(job.job_id, claim.token) is True

        store.set_cancelled(job.job_id, claim.token)
        assert store.get(job.job_id, fresh=True).status == JobStatus.CANCELLED

        retried = store.retry(job.job_id)
        assert retried.status == JobStatus.QUEUED
        assert retried.result is None
        assert retried.cancel_requested is False
        assert source.exists()
        assert store.claim("worker-2", 60) is not None
    finally:
        store.close()


def test_retry_failed_job_and_missing_source(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    source = tmp_path / "contract.txt"
    source.write_text("设备采购合同", encoding="utf-8")
    store = JobStore(db_path)
    try:
        job = store.create(
            "contract.txt", source_path=str(source), cleanup_source=False
        )
        store.set_failed(job.job_id, "boom")
        assert store.retry(job.job_id).status == JobStatus.QUEUED

        source.unlink()
        store.set_failed(job.job_id, "boom again")
        with pytest.raises(SourceMissing):
            store.retry(job.job_id)
    finally:
        store.close()


def test_retry_rejects_completed_job(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        job = store.create("contract.txt")
        store.mark_ready(job.job_id)
        claim = store.claim("worker-1", 60)
        assert claim is not None
        store.set_result(
            job.job_id,
            ReviewResult(
                contract=ContractMeta(filename="contract.txt"),
                summary=build_summary([]),
                clauses=[],
                findings=[],
            ),
            [],
            lease_token=claim.token,
        )
        with pytest.raises(JobNotRetryable):
            store.retry(job.job_id)
    finally:
        store.close()
